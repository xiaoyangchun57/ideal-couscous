"""Focused legacy checkout coverage using disposable DBs and real Flask routes."""
import json

import test_inspection_autonomy as fixtures
import inspection_visit_facts as visits


class LegacyDepartureTest(fixtures.InspectionAutonomyTest):
    def seed(self, *, complete=True, arrival=True, modern=False, realistic=False):
        self.prepare()
        self.evidence_schema()
        if realistic:
            with self.db() as db:
                db.executemany('INSERT INTO inspection_template_items VALUES (?,?,?,?,?,?,?,?)',
                    [(103+n, 10, f'检查{n}', '常规', 0, 0, 1, n+3) for n in range(16)])
        self.assertEqual(self.confirm().status_code, 200)
        if arrival:
            if modern:
                self.assertEqual(self.arrive().status_code, 200)
            else:
                with self.db() as db:
                    db.execute('''INSERT INTO inspection_checkins
                        (site_id,site_name,user_id,user_name,check_time,lat,lng,plan_id)
                        VALUES (1,'Station',2,'Operator',?,28.68,115.73,?)''',
                        (self.day()+' 00:10:00', self.plan_id()))
        with self.db() as db:
            if complete:
                db.execute('''UPDATE insp_plan_items SET result='normal',
                    photo_urls='["preserved.jpg"]',remark='preserved result' ''')
            if realistic:
                items = [r[0] for r in db.execute('SELECT id FROM insp_plan_items ORDER BY id')]
                db.executemany('''INSERT INTO operation_attachments
                    (source_type,source_id,plan_id,item_id,site_id,filename,stored_path)
                    VALUES ('inspection',?,?,?,1,?,?)''',
                    [(items[n % 18], self.plan_id(), items[n % 18],
                      f'synthetic-{n}.jpg', f'/synthetic/{n}.jpg') for n in range(35)])

    def snapshot(self):
        with self.db() as db:
            schema = [tuple(r) for r in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return schema, {t: [dict(r) for r in db.execute('SELECT * FROM "'+t+'" ORDER BY rowid')]
                            for t in tables}

    def old_post(self, expected=200, *, payload=None, token='operator-token', unchanged=False):
        before = self.snapshot()
        response = self.client.post(
            f'/api/mobile/execution-plans/{self.plan_id()}/sites/1/check-out',
            headers=self.headers(token), json=payload if payload is not None else {'lat':28.68,'lng':115.73})
        self.assertEqual(response.status_code, expected, response.json)
        if unchanged:
            self.assertEqual(self.snapshot(), before)
        return response

    def evidence(self):
        with self.db() as db:
            return ([dict(r) for r in db.execute('SELECT * FROM operation_attachments ORDER BY id')],
                    [tuple(r) for r in db.execute('SELECT id,result,photo_urls,remark FROM insp_plan_items ORDER BY id')])

    def test_legacy_table_only_18_checks_35_attachments_and_repeat(self):
        self.seed(realistic=True)
        before = self.evidence()
        with self.db() as db:
            self.assertFalse(visits.events(db, self.plan_id(), 1))
        response = self.old_post()
        self.assertEqual((response.json['completed'], response.json['total']), (18, 18))
        self.assertTrue(response.json['checked_out'])
        self.assertTrue(response.json['can_correct_checkout'])
        self.assertTrue(self.old_post(unchanged=True).json['already_closed'])
        with self.db() as db:
            facts = [dict(r) for r in db.execute('SELECT * FROM inspection_visit_events')]
            self.assertEqual(len(facts), 1)
            fact = facts[0]
            self.assertEqual(fact['event_type'], 'departure')
            self.assertEqual(json.loads(fact['payload'])['source'], 'legacy_missing_version')
            self.assertEqual(json.loads(fact['payload'])['arrival_checkin_id'], 1)
            self.assertTrue(fact['request_key'].endswith(':checkin:1'))
        self.assertEqual(self.evidence(), before)
        self.assertEqual(len(before[0]), 35)

    def test_single_new_arrival_can_use_legacy_departure(self):
        self.seed(modern=True)
        self.old_post()
        with self.db() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT event_type FROM inspection_visit_events ORDER BY id')],
                             ['arrival', 'departure'])

    def test_unscoped_arrival_requires_unique_task(self):
        self.seed()
        with self.db() as db:
            db.execute('UPDATE inspection_checkins SET plan_id=NULL')
        self.old_post()

    def test_unscoped_ambiguous_task_refused(self):
        self.seed()
        with self.db() as db:
            db.execute('UPDATE inspection_checkins SET plan_id=NULL')
            db.execute('''INSERT INTO insp_plans (id,assignee_id,generate_date,status,plan_snapshot)
                SELECT 99,assignee_id,generate_date,'active',plan_snapshot
                FROM insp_plans WHERE id=?''', (self.plan_id(),))
            db.execute("INSERT INTO insp_plan_items (plan_id,site_id,execution_status) VALUES (99,1,'active')")
        self.old_post(409, unchanged=True)

    def test_present_null_empty_and_wrong_versions_remain_protected(self):
        self.seed()
        for version in (None, '', 'wrong', 0):
            with self.subTest(version=version):
                response = self.old_post(409, payload={'lat':28.68,'lng':115.73,'version':version}, unchanged=True)
                self.assertEqual(response.json['code'], 'INSPECTION_VISIT_VERSION_CONFLICT')

    def test_partial_even_explicitly_confirmed_refused(self):
        self.seed(complete=False)
        response = self.old_post(409, payload={'lat':28.68,'lng':115.73,'confirm_incomplete':True}, unchanged=True)
        self.assertIn('全部完成', response.json['error'])
        self.assertIn('保留手机内资料', response.json['error'])
        self.assertNotIn('刷新', response.json['error'])

    def test_no_arrival_refused_without_schema_or_row_changes(self):
        self.seed(arrival=False)
        self.old_post(409, unchanged=True)

    def test_owner_site_scope_and_stopped_task_refused(self):
        self.seed()
        self.old_post(403, token='other-token', unchanged=True)
        with self.db() as db:
            db.execute('DELETE FROM user_sites WHERE user_id=2 AND site_id=1')
        self.old_post(403, unchanged=True)
        with self.db() as db:
            db.execute('INSERT INTO user_sites VALUES (2,1)')
            db.execute("UPDATE plan_schedules SET status='stopped' WHERE id=5")
        self.old_post(403, unchanged=True)

    def test_current_location_required_and_geofenced_even_on_repeat(self):
        self.seed()
        self.old_post(400, payload={}, unchanged=True)
        self.old_post(409, payload={'lat':29,'lng':116}, unchanged=True)
        self.old_post()
        self.old_post(409, payload={'lat':29,'lng':116}, unchanged=True)

    def test_original_arrival_time_and_location_must_be_valid(self):
        self.seed()
        with self.db() as db:
            db.execute('UPDATE inspection_checkins SET lat=29,lng=116')
        self.old_post(409, unchanged=True)
        with self.db() as db:
            db.execute("UPDATE inspection_checkins SET lat=28.68,lng=115.73,check_time='invalid'")
        self.old_post(409, unchanged=True)
        with self.db() as db:
            db.execute('UPDATE inspection_checkins SET check_time=?', (self.day()+' 23:00:00',))
        self.old_post(409, unchanged=True)

    def test_multiple_or_foreign_arrivals_refused(self):
        self.seed()
        with self.db() as db:
            db.execute('''INSERT INTO inspection_checkins (site_id,user_id,check_time,lat,lng,plan_id)
                SELECT site_id,user_id,check_time,lat,lng,plan_id FROM inspection_checkins''')
        self.old_post(409, unchanged=True)
        with self.db() as db:
            db.execute('DELETE FROM inspection_checkins WHERE id=2')
            db.execute('UPDATE inspection_checkins SET user_id=3')
        self.old_post(409, unchanged=True)

    def test_correction_before_display_history_limit_still_refused(self):
        self.seed()
        with self.db() as db:
            visits.record(db, self.plan_id(), 1, 2, 'departure_corrected', self.day()+' 00:11:00', {})
            for _ in range(21):
                visits.record(db, self.plan_id(), 1, 2, 'arrival', self.day()+' 00:10:00', {})
            self.assertNotIn('departure_corrected', [r['event_type'] for r in visits.events(db,self.plan_id(),1)])
        response = self.old_post(409, unchanged=True)
        self.assertIn('已有纠正', response.json['error'])

    def test_after_legacy_correction_delayed_old_cannot_reclose(self):
        self.seed(realistic=True)
        before = self.evidence()
        self.old_post()
        corrected = self.correct()
        self.assertEqual(corrected.status_code, 200, corrected.json)
        self.old_post(409, unchanged=True)
        self.assertFalse(self.visit_state()['checked_out'])
        self.assertEqual(self.evidence(), before)
        self.assertEqual(self.leave().status_code, 200)

    def test_after_legacy_return_old_cannot_close_new_arrival(self):
        self.seed()
        before = self.evidence()
        self.old_post()
        response = self.arrive('actual-return-after-legacy')
        self.assertEqual(response.status_code, 200, response.json)
        self.old_post(409, unchanged=True)
        self.assertFalse(self.visit_state()['checked_out'])
        self.assertEqual(self.leave().status_code, 200)
        self.assertEqual(self.evidence(), before)

    def test_unknown_or_changed_checkout_not_replayed(self):
        self.seed()
        with self.db() as db:
            db.execute('UPDATE insp_plan_items SET check_out_time=?', (self.day()+' 00:12:00',))
        self.old_post(409, unchanged=True)
        with self.db() as db:
            db.execute('UPDATE insp_plan_items SET check_out_time=NULL')
        self.old_post()
        with self.db() as db:
            db.execute('UPDATE insp_plan_items SET check_out_time=NULL')
        self.old_post(409, unchanged=True)

    def test_modern_stale_version_and_partial_confirmation_unchanged(self):
        self.seed(complete=False, modern=True)
        version = self.visit_state()['visit_version']
        self.old_post(409, payload={'lat':28.68,'lng':115.73,'version':version}, unchanged=True)
        with self.db() as db:
            db.execute("UPDATE insp_plan_items SET remark='later fact'")
        self.old_post(409, payload={'lat':28.68,'lng':115.73,'version':version,'confirm_incomplete':True}, unchanged=True)
        response = self.leave(confirm=True)
        self.assertEqual(response.status_code, 200, response.json)

