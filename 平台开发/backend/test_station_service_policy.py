import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
import station_monitoring_api as monitoring_api
from migrate_station_ingestion import apply_migration


class StationServicePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = Path(self.temp_dir.name) / 'station-service-policy.db'
        self.previous_database = web_app.DB_PATH
        self.previous_access_mode = web_app.STATION_MONITORING_ACCESS_MODE
        web_app.DB_PATH = str(self.database)
        web_app.STATION_MONITORING_ACCESS_MODE = 'public'
        web_app.init_db()
        apply_migration(self.database, Path(self.temp_dir.name) / 'backups')
        web_app.migrate_alerts_messages()
        web_app.migrate_alert_flow()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("INSERT INTO sites(code,name,type) VALUES ('POL-1','政策站一','water_quality')")
            db.execute("INSERT INTO sites(code,name,type) VALUES ('POL-2','政策站二','water_quality')")
            self.site_id = db.execute("SELECT id FROM sites WHERE code='POL-1'").fetchone()[0]
            self.other_site_id = db.execute("SELECT id FROM sites WHERE code='POL-2'").fetchone()[0]
            for username, role in (
                    ('policy-admin', 'admin'),
                    ('policy-operator', 'operator'),
                    ('policy-reviewer', 'reviewer')):
                db.execute(
                    'INSERT INTO users(username,password_hash,role,real_name) VALUES (?,?,?,?)',
                    (username, web_app._hash_pw('pw'), role, username),
                )
            users = dict(db.execute(
                "SELECT username,id FROM users WHERE username LIKE 'policy-%'"))
            self.admin_id = users['policy-admin']
            self.operator_id = users['policy-operator']
            self.reviewer_id = users['policy-reviewer']
            db.executemany('INSERT INTO user_roles(user_id,role) VALUES (?,?)', (
                (self.admin_id, 'admin'),
                (self.operator_id, 'operator'),
                (self.reviewer_id, 'reviewer'),
            ))
            db.executemany('INSERT INTO user_sites(user_id,site_id) VALUES (?,?)', (
                (self.operator_id, self.site_id),
                (self.operator_id, self.other_site_id),
                (self.reviewer_id, self.site_id),
            ))
            db.commit()
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.STATION_MONITORING_ACCESS_MODE = self.previous_access_mode
        web_app.DB_PATH = self.previous_database
        self.temp_dir.cleanup()

    def headers(self, username):
        response = self.client.post('/api/auth/login', json={
            'username': username, 'password': 'pw',
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return {'Authorization': 'Bearer ' + response.get_json()['token']}

    def save_policy(self, site_id, *, version, key, service_status='active',
                    reporting_condition='normal', reason='测试变更', confirmed=False):
        return self.client.put(
            f'/api/sites/{site_id}/service-policy',
            headers=self.headers('policy-admin'),
            json={
                'service_status': service_status,
                'reporting_condition': reporting_condition,
                'reason': reason,
                'confirmed': confirmed,
                'expected_version': version,
                'idempotency_key': key,
            },
        )

    def test_default_read_permissions_and_transactional_save_contract(self):
        operator_headers = self.headers('policy-operator')
        default = self.client.get(
            f'/api/sites/{self.site_id}/service-policy', headers=operator_headers)
        self.assertEqual(default.status_code, 200, default.get_json())
        self.assertEqual(default.get_json()['service_status'], 'active')
        self.assertEqual(default.get_json()['reporting_condition'], 'normal')
        self.assertEqual(default.get_json()['version'], 0)
        self.assertFalse(default.get_json()['can_edit'])

        missing = self.client.get(
            '/api/sites/999999/service-policy', headers=self.headers('policy-admin'))
        self.assertEqual(missing.status_code, 404, missing.get_json())
        self.assertEqual(missing.get_json()['code'], 'SITE_NOT_FOUND')

        forbidden = self.client.put(
            f'/api/sites/{self.site_id}/service-policy',
            headers=operator_headers,
            json={
                'service_status': 'active',
                'reporting_condition': 'temporary_stop',
                'reason': '无权修改',
                'expected_version': 0,
                'idempotency_key': 'operator-forbidden',
            },
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.get_json())

        unconfirmed = self.save_policy(
            self.site_id, version=0, key='end-unconfirmed',
            service_status='ended', reason='合同结束')
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.get_json())
        self.assertEqual(unconfirmed.get_json()['code'], 'SERVICE_POLICY_CONFIRM_REQUIRED')

        saved = self.save_policy(
            self.site_id, version=0, key='temporary-stop-1',
            reporting_condition='temporary_stop', reason='现场临时停站')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()['version'], 1)
        self.assertEqual(saved.get_json()['reporting_condition'], 'temporary_stop')

        replay = self.save_policy(
            self.site_id, version=0, key='temporary-stop-1',
            reporting_condition='temporary_stop', reason='现场临时停站')
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertTrue(replay.get_json()['replayed'])

        conflict = self.save_policy(
            self.site_id, version=1, key='temporary-stop-1',
            reporting_condition='normal', reason='恢复')
        self.assertEqual(conflict.status_code, 409, conflict.get_json())
        self.assertEqual(conflict.get_json()['code'], 'SERVICE_POLICY_IDEMPOTENCY_CONFLICT')

        stale = self.save_policy(
            self.site_id, version=0, key='stale-version',
            reporting_condition='normal', reason='恢复')
        self.assertEqual(stale.status_code, 409, stale.get_json())
        self.assertEqual(stale.get_json()['code'], 'SERVICE_POLICY_VERSION_CONFLICT')

        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_service_policy_events WHERE site_id=?',
                (self.site_id,)).fetchone()[0], 1)

    def test_service_ended_scope_archive_and_new_plan_rejection(self):
        admin_headers = self.headers('policy-admin')
        before = self.client.get(
            '/api/station-monitoring/sites?scope=all&service_scope=current',
            headers=admin_headers)
        self.assertEqual(before.status_code, 200, before.get_json())
        self.assertEqual(before.get_json()['summary']['total'], 2)
        self.assertEqual(before.get_json()['service_scope'], 'current')

        ended = self.save_policy(
            self.other_site_id, version=0, key='service-ended-1',
            service_status='ended', reason='服务合同结束', confirmed=True)
        self.assertEqual(ended.status_code, 200, ended.get_json())

        current = self.client.get('/api/sites?service_scope=current', headers=admin_headers)
        self.assertEqual(current.status_code, 200, current.get_json())
        self.assertEqual([row['id'] for row in current.get_json()], [self.site_id])

        all_sites = self.client.get('/api/sites?service_scope=all', headers=admin_headers)
        self.assertEqual(all_sites.status_code, 200, all_sites.get_json())
        ended_row = next(row for row in all_sites.get_json() if row['id'] == self.other_site_id)
        self.assertEqual(ended_row['service_status'], 'ended')

        forbidden_all = self.client.get(
            '/api/sites?service_scope=all', headers=self.headers('policy-operator'))
        self.assertEqual(forbidden_all.status_code, 403, forbidden_all.get_json())

        monitoring_current = self.client.get(
            '/api/station-monitoring/sites?scope=all&service_scope=current',
            headers=admin_headers)
        self.assertEqual(
            [row['site_id'] for row in monitoring_current.get_json()['items']],
            [self.site_id])
        self.assertEqual(monitoring_current.get_json()['summary']['total'], 1)
        self.assertEqual(monitoring_current.get_json()['service_scope'], 'current')

        monitoring_all = self.client.get(
            '/api/station-monitoring/sites?scope=all&service_scope=all',
            headers=admin_headers)
        self.assertEqual(
            {row['site_id'] for row in monitoring_all.get_json()['items']},
            {self.site_id, self.other_site_id})
        self.assertEqual(monitoring_all.get_json()['summary']['total'], 2)
        self.assertEqual(monitoring_all.get_json()['service_scope'], 'all')

        operator_headers = self.headers('policy-operator')
        forbidden_monitoring_all = self.client.get(
            '/api/station-monitoring/sites?scope=mine&service_scope=all',
            headers=operator_headers)
        self.assertEqual(forbidden_monitoring_all.status_code, 403)
        self.assertEqual(
            forbidden_monitoring_all.get_json()['code'],
            'FORBIDDEN_MONITORING_SERVICE_SCOPE')

        invalid_service_scope = self.client.get(
            '/api/station-monitoring/sites?scope=all&service_scope=ended',
            headers=admin_headers)
        self.assertEqual(invalid_service_scope.status_code, 400)
        self.assertEqual(
            invalid_service_scope.get_json()['code'],
            'INVALID_MONITORING_SERVICE_SCOPE')

        archive = self.client.get(
            f'/api/sites/{self.other_site_id}/archive', headers=admin_headers)
        self.assertEqual(archive.status_code, 200, archive.get_json())
        self.assertEqual(archive.get_json()['service_policy']['service_status'], 'ended')

        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            with self.assertRaises(web_app.PlanScheduleSiteScopeError) as raised:
                web_app._ps_validate_execution_sites(db, self.operator_id, {
                    '2026-10-14': {'sites': [self.other_site_id]},
                })
        self.assertEqual(raised.exception.code, 'PLAN_SITE_SERVICE_ENDED')
        self.assertEqual(raised.exception.site_ids, [self.other_site_id])

    def test_site_and_monitoring_scopes_share_the_26_and_36_station_denominators(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.executemany(
                "INSERT INTO sites(code,name,type) VALUES (?,?, 'water_quality')",
                ((f'POL-{index}', f'政策站{index}',) for index in range(3, 37)),
            )
            site_ids = [row[0] for row in db.execute('SELECT id FROM sites ORDER BY id')]
            current_ids = set(site_ids[:26])
            ended_ids = set(site_ids[26:])
            db.executemany("""INSERT INTO station_service_policy_events(
                    site_id,version,previous_service_status,service_status,
                    previous_reporting_condition,reporting_condition,effective_from,reason,
                    changed_by_user_id,idempotency_key,request_fingerprint,created_at)
                VALUES (?,1,'active','ended','normal','normal',?,'合同结束',?,?,?,?)""", (
                (site_id, '2026-10-16T01:05:00+00:00', self.admin_id,
                 f'end-{site_id}', f'end-{site_id}', '2026-10-16T01:05:00+00:00')
                for site_id in sorted(ended_ids)
            ))
            ended_assigned_id = min(ended_ids)
            db.execute(
                'INSERT INTO user_sites(user_id,site_id) VALUES (?,?)',
                (self.operator_id, ended_assigned_id),
            )
            policy_rows = db.execute(
                'SELECT id,site_id FROM station_service_policy_events ORDER BY id'
            ).fetchall()
            self.assertTrue(policy_rows)
            self.assertTrue(all(event_id != site_id for event_id, site_id in policy_rows))
            db.commit()
        current_order = sorted(current_ids)
        all_order = sorted(current_ids | ended_ids)
        statuses = {}
        for site_id in current_order[:2]:
            statuses[site_id] = 'not_connected'
        for site_id in current_order[2:19]:
            statuses[site_id] = 'raw_received_config_pending'
        for site_id in current_order[19:]:
            statuses[site_id] = 'waiting_first_valid'
        statuses[all_order[26]] = 'not_connected'
        for site_id in all_order[27:]:
            statuses[site_id] = 'raw_received_config_pending'

        def projections(_db, rows, _projection_version):
            return [{
                'site_id': row['id'],
                'monitoring_status': statuses[row['id']],
            } for row in rows]

        headers = self.headers('policy-admin')
        with mock.patch.object(
                monitoring_api, '_station_monitoring_batch_projections_cached',
                side_effect=projections):
            current_sites = self.client.get(
                '/api/sites?service_scope=current', headers=headers).get_json()
            current_monitoring = self.client.get(
                '/api/station-monitoring/sites?scope=all&service_scope=current',
                headers=headers).get_json()
            all_sites = self.client.get(
                '/api/sites?service_scope=all', headers=headers).get_json()
            all_monitoring = self.client.get(
                '/api/station-monitoring/sites?scope=all&service_scope=all',
                headers=headers).get_json()

        self.assertEqual(len(current_sites), 26)
        self.assertEqual(current_monitoring['summary']['total'], 26)
        self.assertEqual(current_monitoring['summary']['not_connected'], 2)
        self.assertEqual(current_monitoring['summary']['raw_received_config_pending'], 17)
        self.assertEqual(current_monitoring['summary']['waiting_first_valid'], 7)
        self.assertEqual(len(all_sites), 36)
        self.assertEqual(all_monitoring['summary']['total'], 36)
        self.assertEqual(all_monitoring['summary']['not_connected'], 3)
        self.assertEqual(all_monitoring['summary']['raw_received_config_pending'], 26)
        self.assertEqual(all_monitoring['summary']['waiting_first_valid'], 7)

        mobile_admin = self.client.get(
            '/api/mobile/responsible-sites?scope=all', headers=headers)
        self.assertEqual(mobile_admin.status_code, 200, mobile_admin.get_json())
        mobile_admin_body = mobile_admin.get_json()
        self.assertEqual(mobile_admin_body['scope_counts']['all'], 26)
        self.assertEqual(
            {item['site_id'] for item in mobile_admin_body['items']}, current_ids)
        self.assertEqual(
            [item['name'] for item in mobile_admin_body['items']],
            sorted(item['name'] for item in mobile_admin_body['items']),
        )
        self.assertEqual(
            {item['site_id'] for item in mobile_admin_body['items']},
            {item['id'] for item in current_sites},
        )

        mobile_keyword = self.client.get(
            '/api/mobile/responsible-sites', headers=headers,
            query_string={'scope': 'all', 'keyword': '政策站3'},
        )
        self.assertEqual(mobile_keyword.status_code, 200, mobile_keyword.get_json())
        self.assertEqual(
            [item['site_id'] for item in mobile_keyword.get_json()['items']],
            [current_order[2]],
        )

        operator_headers = self.headers('policy-operator')
        mobile_mine = self.client.get(
            '/api/mobile/responsible-sites?scope=mine', headers=operator_headers)
        self.assertEqual(mobile_mine.status_code, 200, mobile_mine.get_json())
        self.assertEqual(
            {item['site_id'] for item in mobile_mine.get_json()['items']},
            {self.site_id, self.other_site_id},
        )
        self.assertEqual(mobile_mine.get_json()['scope_counts']['mine'], 2)
        forbidden_mobile_all = self.client.get(
            '/api/mobile/responsible-sites?scope=all', headers=operator_headers)
        self.assertEqual(
            forbidden_mobile_all.status_code, 403, forbidden_mobile_all.get_json())

        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO station_service_policy_events(
                    site_id,version,previous_service_status,service_status,
                    previous_reporting_condition,reporting_condition,effective_from,reason,
                    changed_by_user_id,idempotency_key,request_fingerprint,created_at)
                VALUES (?,1,'active','ended','normal','normal',?,'合同结束',?,?,?,?)""", (
                self.other_site_id, '2026-10-16T01:06:00+00:00', self.admin_id,
                'end-current-mobile-site', 'end-current-mobile-site',
                '2026-10-16T01:06:00+00:00',
            ))
            db.commit()

        mobile_after_end = self.client.get(
            '/api/mobile/responsible-sites?scope=all', headers=headers)
        self.assertEqual(
            mobile_after_end.status_code, 200, mobile_after_end.get_json())
        self.assertEqual(mobile_after_end.get_json()['scope_counts']['all'], 25)
        self.assertNotIn(
            self.other_site_id,
            {item['site_id'] for item in mobile_after_end.get_json()['items']},
        )
        mobile_mine_after_end = self.client.get(
            '/api/mobile/responsible-sites?scope=mine', headers=operator_headers)
        self.assertEqual(
            mobile_mine_after_end.status_code, 200,
            mobile_mine_after_end.get_json())
        self.assertEqual(
            [item['site_id'] for item in mobile_mine_after_end.get_json()['items']],
            [self.site_id],
        )
        self.assertEqual(mobile_mine_after_end.get_json()['scope_counts']['mine'], 1)

    def test_policy_history_controls_arrival_denominator_without_hiding_values(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            db.executemany("""INSERT INTO station_service_policy_events(
                    site_id,version,previous_service_status,service_status,
                    previous_reporting_condition,reporting_condition,effective_from,reason,
                    changed_by_user_id,idempotency_key,request_fingerprint,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                (self.site_id, 1, 'active', 'active', 'normal', 'temporary_stop',
                 '2026-10-12T04:00:00+00:00', '临时停站', self.admin_id,
                 'history-stop', 'history-stop', '2026-10-12T04:00:00+00:00'),
                (self.site_id, 2, 'active', 'active', 'temporary_stop', 'normal',
                 '2026-10-12T08:00:00+00:00', '恢复到报', self.admin_id,
                 'history-resume', 'history-resume', '2026-10-12T08:00:00+00:00'),
            ))
            db.commit()
            configuration = {
                'endpoint_id': 10,
                'business_metric': 'ph',
                'instrument_asset_code': None,
            }
            slots = [
                {'scheduled_at': '2026-10-12T02:00:00+00:00', 'result_window_end_at': '2026-10-12T02:10:00+00:00'},
                {'scheduled_at': '2026-10-12T06:00:00+00:00', 'result_window_end_at': '2026-10-12T06:10:00+00:00'},
                {'scheduled_at': '2026-10-12T10:00:00+00:00', 'result_window_end_at': '2026-10-12T10:10:00+00:00'},
            ]
            observations = [
                {
                    'business_site_id': self.site_id,
                    'endpoint_id': 10,
                    'business_metric': 'ph',
                    'instrument_asset_code': None,
                    'scheduled_at': slot['scheduled_at'],
                }
                for slot in slots
            ]
            with mock.patch.object(
                    web_app, 'monitoring_factor_configurations_batch',
                    return_value={self.site_id: [configuration]}), mock.patch.object(
                    web_app, 'monitoring_expected_business_slots', return_value=slots), mock.patch.object(
                    web_app, '_formal_business_observation_rows', return_value=observations):
                summary = web_app._formal_arrival_summary(
                    db, '2026-10-12T00:00:00+00:00', '2026-10-13T00:00:00+00:00',
                    [self.site_id], evaluated_at='2026-10-13T00:00:00+00:00')
        self.assertEqual(summary['by_metric'][0]['expected_count'], 2)
        self.assertEqual(summary['by_metric'][0]['actual_count'], 2)
        self.assertEqual(summary['total_avg'], 100.0)

        trend_points = [
            {
                'business_metric': 'ph',
                'scheduled_at': slot['scheduled_at'], 'observed_at': slot['scheduled_at'],
                'status': 'value', 'value': 7.1,
                'unit': '',
                'quality': 'valid',
                'received_at': None,
                'deadline_at': slot['result_window_end_at'],
            }
            for slot in slots[:1]
        ]
        trend_configuration = {
            **configuration,
            'expected_interval_seconds': 14400,
            'schedule_id': 1,
            'standard_unit': '',
        }
        with mock.patch.object(
                monitoring_api, '_monitoring_site_context', return_value=({}, None)), mock.patch.object(
                monitoring_api, 'monitoring_factor_configurations',
                return_value=[trend_configuration]), mock.patch.object(
                monitoring_api, 'monitoring_result_trend_page',
                return_value=(trend_points, False, None)), mock.patch.object(
                monitoring_api, 'monitoring_business_slot_diagnostics', return_value={
                    'late_points': 0, 'suspect_points': 0,
                    'duplicate_records': 0, 'conflict_slots': 0,
                }), mock.patch.object(
                monitoring_api, 'monitoring_expected_business_slots', return_value=slots):
            trend = self.client.get(
                f'/api/station-monitoring/sites/{self.site_id}/trend',
                headers=self.headers('policy-admin'),
                query_string={
                    'metric': 'ph',
                    'start': '2026-10-12T00:00:00+00:00',
                    'end': '2026-10-13T00:00:00+00:00',
                },
            )
        self.assertEqual(trend.status_code, 200, trend.get_json())
        self.assertEqual(
            [point['scheduled_at'] for point in trend.get_json()['points']],
            ['2026-10-12T02:00:00+00:00'],
        )
        self.assertEqual(trend.get_json()['coverage']['expected_points'], 2)
        self.assertEqual(trend.get_json()['coverage']['missing_points'], 1)

    def test_service_ended_rejects_only_new_dispatch_with_zero_side_effects(self):
        ended = self.save_policy(
            self.other_site_id, version=0, key='dispatch-ended',
            service_status='ended', reason='服务合同结束', confirmed=True)
        self.assertEqual(ended.status_code, 200, ended.get_json())

        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO work_orders(
                    order_no,site_id,source,event_type,level,title,description,assignee,status)
                VALUES ('WO-POLICY-EXISTING',?,'manual','现场处置','normal',
                        '结束站已有工单','原有工单继续处理','policy-operator','pending')""",
                       (self.other_site_id,))
            alert_rows = (
                (self.other_site_id, '结束站复核转单'),
                (self.other_site_id, '结束站直接转单'),
                (self.other_site_id, '结束站关闭告警'),
                (self.site_id, '在服务站复核转单'),
                (self.site_id, '在服务站直接转单'),
            )
            db.executemany("""INSERT INTO alerts(
                    site_id,metric,value,level,message,status,flow_type,flow_status)
                VALUES (?,'ph',7.1,'yellow',?,'pending','manual','pending_review')""",
                           alert_rows)
            db.commit()
            alert_ids = [row[0] for row in db.execute(
                'SELECT id FROM alerts ORDER BY id').fetchall()]

        admin_headers = self.headers('policy-admin')
        duplicate = self.client.post('/api/workorders', headers=admin_headers, json={
            'site_id': self.other_site_id,
            'assignee_id': self.operator_id,
            'source': 'manual',
            'title': '结束站已有工单',
            'event_type': '现场处置',
        })
        self.assertEqual(duplicate.status_code, 200, duplicate.get_json())
        self.assertTrue(duplicate.get_json()['duplicate'])
        self.assertEqual(duplicate.get_json()['order_no'], 'WO-POLICY-EXISTING')

        with closing(sqlite3.connect(self.database)) as db:
            before_counts = {
                table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ('work_orders', 'timeline_events', 'notifications')
            }
            before_alerts = db.execute("""SELECT id,status,flow_status,related_order_no
                FROM alerts WHERE id IN (?,?) ORDER BY id""", alert_ids[:2]).fetchall()

        blocked_responses = (
            self.client.post('/api/workorders', headers=admin_headers, json={
                'site_id': self.other_site_id,
                'assignee_id': self.operator_id,
                'source': 'manual',
                'title': '结束站新工单',
                'event_type': '现场处置',
            }),
            self.client.post(
                f'/api/alerts/{alert_ids[0]}/confirm-convert',
                headers=admin_headers, json={'action': 'convert'}),
            self.client.post(
                f'/api/alerts/{alert_ids[1]}/convert-order',
                headers=admin_headers, json={}),
        )
        for response in blocked_responses:
            self.assertEqual(response.status_code, 409, response.get_json())
            self.assertEqual(
                response.get_json()['code'], 'SITE_SERVICE_ENDED_NEW_DISPATCH')

        with closing(sqlite3.connect(self.database)) as db:
            after_counts = {
                table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ('work_orders', 'timeline_events', 'notifications')
            }
            after_alerts = db.execute("""SELECT id,status,flow_status,related_order_no
                FROM alerts WHERE id IN (?,?) ORDER BY id""", alert_ids[:2]).fetchall()
        self.assertEqual(after_counts, before_counts)
        self.assertEqual(after_alerts, before_alerts)

        dismissed = self.client.post(
            f'/api/alerts/{alert_ids[2]}/confirm-convert',
            headers=admin_headers,
            json={'action': 'dismiss', 'remark': '确认无需派单'},
        )
        self.assertEqual(dismissed.status_code, 200, dismissed.get_json())
        self.assertEqual(dismissed.get_json()['action'], 'dismissed')

        active_responses = (
            self.client.post('/api/workorders', headers=admin_headers, json={
                'site_id': self.site_id,
                'assignee_id': self.operator_id,
                'source': 'manual',
                'title': '在服务站新工单',
                'event_type': '现场处置',
            }),
            self.client.post(
                f'/api/alerts/{alert_ids[3]}/confirm-convert',
                headers=admin_headers, json={'action': 'convert'}),
            self.client.post(
                f'/api/alerts/{alert_ids[4]}/convert-order',
                headers=admin_headers, json={}),
        )
        for response in active_responses:
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertTrue(response.get_json()['success'])

        restored = self.save_policy(
            self.other_site_id, version=1, key='dispatch-restored',
            service_status='active', reason='恢复运维服务')
        self.assertEqual(restored.status_code, 200, restored.get_json())
        resumed = self.client.post('/api/workorders', headers=admin_headers, json={
            'site_id': self.other_site_id,
            'assignee_id': self.operator_id,
            'source': 'manual',
            'title': '恢复后新工单',
            'event_type': '现场处置',
        })
        self.assertEqual(resumed.status_code, 200, resumed.get_json())
        self.assertTrue(resumed.get_json()['success'])

    def test_admin_workorder_assignee_is_selected_by_identity_and_site_scope(self):
        admin_headers = self.headers('policy-admin')
        assignees = self.client.get('/api/assignees', headers=admin_headers)
        self.assertEqual(assignees.status_code, 200, assignees.get_json())
        operator = next(item for item in assignees.get_json() if item['id'] == self.operator_id)
        self.assertEqual(set(operator['site_ids']), {self.site_id, self.other_site_id})

        created = self.client.post('/api/workorders', headers=admin_headers, json={
            'site_id': self.site_id, 'assignee_id': self.operator_id,
            'source': 'manual', 'title': '负责人身份校验成功',
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        with closing(sqlite3.connect(self.database)) as db:
            assigned = db.execute('SELECT assignee FROM work_orders WHERE order_no=?',
                                  (created.get_json()['order_no'],)).fetchone()[0]
            before_invalid = db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0]
            db.execute("INSERT INTO users(username,password_hash,role,real_name,status) VALUES ('no-site-operator',?,'operator','无站点运维','active')",
                       (web_app._hash_pw('pw'),))
            no_site_id = db.execute("SELECT id FROM users WHERE username='no-site-operator'").fetchone()[0]
            db.execute("INSERT INTO user_roles(user_id,role) VALUES (?,'operator')", (no_site_id,))
            db.execute("INSERT INTO users(username,password_hash,role,real_name,status) VALUES ('inactive-operator',?,'operator','停用运维','inactive')",
                       (web_app._hash_pw('pw'),))
            inactive_id = db.execute("SELECT id FROM users WHERE username='inactive-operator'").fetchone()[0]
            db.execute("INSERT INTO user_roles(user_id,role) VALUES (?,'operator')", (inactive_id,))
            db.execute('INSERT INTO user_sites(user_id,site_id) VALUES (?,?)', (inactive_id, self.site_id))
            db.commit()
        self.assertEqual(assigned, 'policy-operator')

        invalid_cases = (
            ({}, 'WORKORDER_ASSIGNEE_REQUIRED'),
            ({'assignee_id': 999999}, 'WORKORDER_ASSIGNEE_INACTIVE'),
            ({'assignee_id': inactive_id}, 'WORKORDER_ASSIGNEE_INACTIVE'),
            ({'assignee_id': self.reviewer_id}, 'WORKORDER_ASSIGNEE_ROLE_INVALID'),
            ({'assignee_id': no_site_id}, 'WORKORDER_ASSIGNEE_SITE_FORBIDDEN'),
        )
        for index, (extra, expected_code) in enumerate(invalid_cases):
            response = self.client.post('/api/workorders', headers=admin_headers, json={
                'site_id': self.site_id, 'source': 'manual',
                'title': f'无效负责人{index}', **extra,
            })
            self.assertIn(response.status_code, (400, 409), response.get_json())
            self.assertEqual(response.get_json()['code'], expected_code)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0], before_invalid)

    def test_offline_keeps_device_alert_but_suppresses_new_data_gap(self):
        paused = self.save_policy(
            self.site_id, version=0, key='offline-paused',
            reporting_condition='no_upload_condition', reason='现场暂无上传条件')
        self.assertEqual(paused.status_code, 200, paused.get_json())
        ended = self.save_policy(
            self.other_site_id, version=0, key='offline-ended',
            service_status='ended', reason='服务已结束', confirmed=True)
        self.assertEqual(ended.status_code, 200, ended.get_json())
        with closing(sqlite3.connect(self.database)) as db:
            db.executemany("""INSERT INTO device_shadows(
                    site_id,device_code,device_name,status,last_data_time,monitoring_enabled)
                VALUES (?,?,?,'online','2000-01-01 00:00:00',1)""", (
                (self.site_id, 'POL-DEV-1', '暂停站技术设备'),
                (self.other_site_id, 'POL-DEV-2', '结束服务站技术设备'),
            ))
            db.commit()

        with mock.patch.object(web_app, '_wx_push_alert') as push_alert:
            web_app.check_device_offline()

        with closing(sqlite3.connect(self.database)) as db:
            metrics = [row[0] for row in db.execute(
                "SELECT metric FROM alerts WHERE site_id=? ORDER BY id", (self.site_id,))]
            self.assertIn('device_status', metrics)
            self.assertNotIn('data_gap', metrics)
            ended_alert = db.execute(
                "SELECT metric,flow_type FROM alerts WHERE site_id=? ORDER BY id DESC LIMIT 1",
                (self.other_site_id,)).fetchone()
            self.assertEqual(ended_alert, ('device_status', 'manual'))
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM work_orders WHERE site_id=?',
                (self.other_site_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT status FROM device_shadows WHERE device_code='POL-DEV-1'").fetchone()[0],
                'offline')
            self.assertEqual(db.execute(
                "SELECT status FROM device_shadows WHERE device_code='POL-DEV-2'").fetchone()[0],
                'offline')
        self.assertNotIn(
            self.other_site_id,
            [call.args[0] for call in push_alert.call_args_list],
        )


if __name__ == '__main__':
    unittest.main()
