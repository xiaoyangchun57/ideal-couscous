import unittest
import test_inspection_review_rework as fixtures
import test_mobile_checkin_integrity as checkin_fixtures


class PhotoReworkContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InspectionReviewReworkTest()
        self.fixture.setUp()
        self.app = fixtures.app_module
        self.client = self.fixture.client
        with self.app.get_db() as db:
            db.execute("""UPDATE insp_plan_items SET result='normal',
                completed_at='2026-09-21 10:00:00',review_status=1 WHERE id=100""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,filename,review_status,source_type,source_id,
                 evidence_qualification,extra_json,plan_id,item_id)
                VALUES (220,'/uploads/inspection/quality.jpg',1,2,'quality.jpg','pending',
                        'inspection',100,'ineligible','{"material_role":"formal"}',10,100)""")

    def tearDown(self):
        self.fixture.tearDown()

    def test_quality_rejection_does_not_reopen_item_and_replay_does_not_charge_twice(self):
        headers = self.fixture.headers('admin-token')
        for _ in range(2):
            response = self.client.post('/api/inspection/photos/220/review', headers=headers,
                json={'action': 'reject', 'reject_reason': 'blurred', '_idempotency_key': 'photo-220'})
            self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.get_db() as db:
            item = db.execute('SELECT result,completed_at,review_status FROM insp_plan_items WHERE id=100').fetchone()
            self.assertEqual(tuple(item), ('normal', '2026-09-21 10:00:00', 2))
            quality = db.execute('SELECT status,score_month FROM photo_quality_evaluations').fetchall()
            self.assertEqual([tuple(row) for row in quality], [('unqualified', '2026-09')])

    def test_batch_review_replay_does_not_double_charge(self):
        for _ in range(2):
            response = self.client.post('/api/operation-attachments/review',
                headers=self.fixture.headers('admin-token'), json={
                    'reject_ids': [220], 'approve_ids': [], 'approve_item_ids': [100],
                    'reject_reason': '照片模糊', '_idempotency_key': 'batch-quality-220'})
            self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM photo_quality_evaluations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT review_status FROM insp_plan_items WHERE id=100').fetchone()[0], 2)
        for url, token in [('/api/evaluation/personnel/2/photo-quality', 'admin-token'),
                           ('/api/mobile/photo-quality-monthly', 'operator-token')]:
            response = self.client.get(url, headers=self.fixture.headers(token),
                                       query_string={'month': '2026-09'})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.json['deduction'], 1)
            photo = response.json['photos'][0]
            self.assertEqual((photo['attachment_id'], photo['plan_id'], photo['item_id'], photo['reason']),
                             (220, 10, 100, '照片模糊'))
            self.assertTrue(photo['display_url'])

    def test_single_item_photo_selection_records_quality_and_completes_review(self):
        response = self.client.post('/api/operation-attachments/review',
            headers=self.fixture.headers('admin-token'), json={
                'reject_ids': [220], 'approve_ids': [], 'approve_item_ids': [],
                'reject_reason': '照片模糊'})
        self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.get_db() as db:
            self.assertEqual(db.execute('SELECT review_status FROM insp_plan_items WHERE id=100').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT status FROM photo_quality_evaluations WHERE attachment_id=220').fetchone()[0], 'unqualified')
        response = self.client.get('/api/mobile/photo-quality-monthly?month=2026-09',
            headers=self.fixture.headers('operator-token'))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.json['deduction'], 1)

    def test_unqualified_photo_can_be_reviewed_qualified_without_forging_source(self):
        response = self.client.post('/api/inspection/photos/220/review',
            headers=self.fixture.headers('admin-token'), json={'action': 'approve'})
        self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.get_db() as db:
            row = db.execute('SELECT review_status,evidence_qualification FROM operation_attachments WHERE id=220').fetchone()
            self.assertEqual(tuple(row), ('approved', 'ineligible'))

    def test_completion_keeps_missing_item_and_real_rate(self):
        visit = checkin_fixtures.MobileCheckinIntegrityTest()
        visit.setUp()
        try:
            with self.app.get_db() as db:
                db.execute('ALTER TABLE insp_plans ADD COLUMN completion_rate REAL DEFAULT 0')
            headers = visit.headers('operator-token')
            checked = visit.client.post('/api/mobile/check-in', headers=headers,
                json={'site_id': 1, 'plan_id': 20, 'lat': 28.6801, 'lng': 115.7301})
            self.assertEqual(checked.status_code, 200, checked.get_json())
            response = visit.client.post('/api/mobile/execution-plans/20/sites/1/check-out',
                headers=headers, json={'lat': 28.6801, 'lng': 115.7301})
            self.assertEqual(response.status_code, 200, response.get_json())
            with self.app.get_db() as db:
                self.assertIsNone(db.execute('SELECT result FROM insp_plan_items WHERE id=30').fetchone()[0])
                plan = db.execute('SELECT status,completion_rate FROM insp_plans WHERE id=20').fetchone()
                self.assertEqual(tuple(plan), ('active', 0))
                remaining = db.execute('SELECT COUNT(*) FROM insp_plan_items WHERE plan_id=20 AND check_out_time IS NULL').fetchone()[0]
                self.assertEqual(remaining, 2)
                db.execute("UPDATE insp_plan_items SET check_out_time=datetime('now','localtime') WHERE plan_id=20 AND site_id!=1")
                self.app._ps_recalculate_field_execution(db, 20)
                plan = db.execute('SELECT status,completion_rate FROM insp_plans WHERE id=20').fetchone()
                self.assertEqual(tuple(plan), ('completed', 0))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plan_items WHERE plan_id=20 AND result IS NULL').fetchone()[0], 3)
        finally:
            visit.tearDown()

    def test_legacy_supplement_request_preserves_submitted_result_and_photo(self):
        with self.app.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("UPDATE insp_plan_items SET evidence_status='supplement_required',review_status=3 WHERE id=100")
            before = tuple(db.execute('SELECT result,completed_at,photo_urls,review_status FROM insp_plan_items WHERE id=100').fetchone())
        response = self.client.post('/api/mobile/submit-item',
            headers=self.fixture.headers('operator-token'),
            json={'item_id': 100, 'result': 'abnormal', 'supplement': True})
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()['code'], 'INSPECTION_ITEM_ALREADY_SUBMITTED')
        with self.app.get_db() as db:
            self.assertEqual(tuple(db.execute('SELECT result,completed_at,photo_urls,review_status FROM insp_plan_items WHERE id=100').fetchone()), before)

    def test_legacy_quality_flags_do_not_bypass_checkin(self):
        visit = checkin_fixtures.MobileCheckinIntegrityTest()
        visit.setUp()
        try:
            with self.app.get_db() as db:
                db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
                db.execute('ALTER TABLE insp_plan_items ADD COLUMN review_status INTEGER DEFAULT 0')
                db.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'operator'")
                db.execute("UPDATE insp_plan_items SET evidence_status='supplement_required' WHERE id=30")
            response = visit.client.post('/api/mobile/submit-item',
                headers=visit.headers('operator-token'),
                json={'item_id': 30, 'result': 'normal', 'supplement': True})
            self.assertEqual(response.status_code, 400, response.get_json())
            checked = visit.client.post('/api/mobile/check-in', headers=visit.headers('operator-token'),
                json={'site_id': 1, 'plan_id': 20, 'lat': 28.6801, 'lng': 115.7301})
            self.assertEqual(checked.status_code, 200, checked.get_json())
            response = visit.client.post('/api/mobile/submit-item',
                headers=visit.headers('operator-token'),
                json={'item_id': 30, 'result': 'normal', 'supplement': True})
            self.assertEqual(response.status_code, 200, response.get_json())
        finally:
            visit.tearDown()

    def test_monthly_deduction_drilldown_keeps_owner_and_rejudgment_in_sync(self):
        reviewed = self.client.post('/api/inspection/photos/220/review',
            headers=self.fixture.headers('admin-token'), json={'action': 'reject', 'reject_reason': '照片模糊'})
        self.assertEqual(reviewed.status_code, 200, reviewed.get_json())
        mine = self.client.get('/api/mobile/photo-quality-monthly?month=2026-09',
            headers=self.fixture.headers('operator-token'))
        self.assertEqual(mine.status_code, 200, mine.get_json())
        self.assertEqual(mine.json['deduction'], 1)
        self.assertEqual(mine.json['photos'][0]['reason'], '照片模糊')
        self.assertEqual(mine.json['photos'][0]['plan_id'], 10)
        self.assertTrue(mine.json['photos'][0]['display_url'])
        team = self.client.get('/api/evaluation/personnel/2/photo-quality?month=2026-09',
            headers=self.fixture.headers('admin-token'))
        self.assertEqual(team.json['deduction'], mine.json['deduction'])
        denied = self.client.get('/api/evaluation/personnel/1/photo-quality?month=2026-09',
            headers=self.fixture.headers('operator-token'))
        self.assertEqual(denied.status_code, 403)
        other_month = self.client.get('/api/mobile/photo-quality-monthly?month=2026-10',
            headers=self.fixture.headers('operator-token'))
        self.assertEqual((other_month.json['deduction'], other_month.json['photos']), (0, []))
        with self.app.get_db() as db:
            db.execute('UPDATE insp_plans SET assignee_id=1 WHERE id=10')
        retained = self.client.get('/api/mobile/photo-quality-monthly?month=2026-09',
            headers=self.fixture.headers('operator-token'))
        self.assertEqual(retained.json['deduction'], 1)
        approved = self.client.post('/api/inspection/photos/220/review',
            headers=self.fixture.headers('admin-token'), json={'action': 'approve'})
        self.assertEqual(approved.status_code, 200, approved.get_json())
        corrected = self.client.get('/api/mobile/photo-quality-monthly?month=2026-09',
            headers=self.fixture.headers('operator-token'))
        self.assertEqual((corrected.json['deduction'], corrected.json['photos']), (0, []))

    def test_monthly_snapshot_survives_time_changes_clearing_and_rejudgment(self):
        admin = self.fixture.headers('admin-token')
        operator = self.fixture.headers('operator-token')
        with self.app.get_db() as db:
            db.executescript("""
                ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active';
                ALTER TABLE users ADD COLUMN phone TEXT;
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE work_orders (assignee TEXT,status TEXT,resolved_at TEXT,
                    created_at TEXT,check_in_time TEXT,sla_deadline TEXT,site_id INTEGER);
            """)

        def assert_month(month, expected):
            start, end = month + '-01', month + '-30'
            summary = self.client.get('/api/evaluation/personnel', headers=admin,
                query_string={'period': 'custom', 'start': start, 'end': end})
            self.assertEqual(summary.status_code, 200, summary.get_json())
            person = next(row for row in summary.json['list'] if row['id'] == 2)
            self.assertEqual(person['photo_quality_deduction'], expected)
            for url, headers in [('/api/evaluation/personnel/2/photo-quality', admin),
                                 ('/api/mobile/photo-quality-monthly', operator)]:
                response = self.client.get(url, headers=headers, query_string={'month': month})
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.json['deduction'], expected)
                self.assertEqual(len(response.json['photos']), expected)
                if expected:
                    self.assertEqual(response.json['photos'][0]['score_month'], '2026-09')

        rejected = self.client.post('/api/inspection/photos/220/review', headers=admin,
            json={'action': 'reject', 'reject_reason': 'blurred'})
        self.assertEqual(rejected.status_code, 200, rejected.get_json())
        for changed_time in ['2026-10-02 10:00:00', None]:
            with self.app.get_db() as db:
                db.execute('UPDATE insp_plan_items SET completed_at=? WHERE id=100', (changed_time,))
            assert_month('2026-09', 1)
            assert_month('2026-10', 0)
        approved = self.client.post('/api/inspection/photos/220/review', headers=admin,
            json={'action': 'approve'})
        self.assertEqual(approved.status_code, 200, approved.get_json())
        assert_month('2026-09', 0)
        assert_month('2026-10', 0)
        with self.app.get_db() as db:
            snapshots = db.execute('SELECT inspection_completed_at,score_month,attribution_status '
                'FROM photo_quality_evaluations WHERE attachment_id=220').fetchall()
            self.assertTrue(all(tuple(row) == ('2026-09-21 10:00:00', '2026-09', 'reliable')
                                for row in snapshots))

    def test_unknown_history_does_not_become_scored_after_time_is_filled(self):
        from photo_quality import current_photo_quality
        admin = self.fixture.headers('admin-token')
        with self.app.get_db() as db:
            db.execute('UPDATE insp_plan_items SET completed_at=NULL WHERE id=100')
        response = self.client.post('/api/inspection/photos/220/review', headers=admin,
            json={'action': 'reject', 'reject_reason': 'historical unknown'})
        self.assertEqual(response.status_code, 200, response.get_json())
        with self.app.get_db() as db:
            db.execute("UPDATE insp_plan_items SET completed_at='2026-10-02 10:00:00' WHERE id=100")
            rows = current_photo_quality(db, 2, '2026-10-01', '2026-10-31 23:59:59', include_unknown=True)
            row = next(row for row in rows if row['attachment_id'] == 220)
            self.assertEqual((row['attribution_status'], row['deduction'], row['score_month']),
                             ('unknown', 0, ''))
        for action in ['approve', 'reject']:
            response = self.client.post('/api/inspection/photos/220/review', headers=admin,
                json={'action': action, 'reject_reason': 'still unknown'})
            self.assertEqual(response.status_code, 200, response.get_json())
            for url in ['/api/evaluation/personnel/2/photo-quality', '/api/mobile/photo-quality-monthly']:
                headers = admin if '/personnel/' in url else self.fixture.headers('operator-token')
                result = self.client.get(url, headers=headers, query_string={'month': '2026-10'})
                self.assertEqual((result.json['deduction'], result.json['photos']), (0, []))

    def test_operator_cannot_review_own_photo(self):
        response = self.client.post('/api/inspection/photos/220/review',
            headers=self.fixture.headers('operator-token'), json={'action': 'approve'})
        self.assertEqual(response.status_code, 403)
        with self.app.get_db() as db:
            self.assertEqual(db.execute('SELECT review_status FROM operation_attachments WHERE id=220').fetchone()[0], 'pending')

    def test_quality_opinion_does_not_erase_execution_or_complete_missing_item(self):
        with self.app.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("UPDATE insp_plan_items SET review_status=3,evidence_status='supplement_required' WHERE id=100")
            item = db.execute('SELECT * FROM insp_plan_items WHERE id=100').fetchone()
            self.assertTrue(self.app._inspection_field_item_completed(item))
            predicate = self.app._inspection_field_completion_sql(db)
            self.assertEqual(db.execute(f'SELECT COUNT(*) FROM insp_plan_items WHERE id=100 AND {predicate}').fetchone()[0], 1)
            self.assertEqual(self.app._inspection_active_rework_sql(db), '0')
            db.execute('UPDATE insp_plan_items SET result=NULL WHERE id=100')
            item = db.execute('SELECT * FROM insp_plan_items WHERE id=100').fetchone()
            self.assertFalse(self.app._inspection_field_item_completed(item))
