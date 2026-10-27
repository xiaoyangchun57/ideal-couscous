"""Bounded incident regression using exported 9/3/3 normalized facts."""
import json
import sqlite3
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import closing
from unittest import mock

from backend import test_station_monitoring_normalization as fixture_module
import station_monitoring as core
import station_monitoring_api as api
web_app = fixture_module.web_app

FACTS = json.loads((Path(__file__).parent / 'fixtures/station_result_chain_20261022.json').read_text(encoding='utf-8'))
AT = FACTS['evidence_time']['t0_utc']


class CountingCursor:
    def __init__(self, cursor, counter, key):
        self.cursor, self.counter, self.key = cursor, counter, key

    def __iter__(self):
        for row in self.cursor:
            if self.key:
                self.counter[self.key] += 1
            yield row

    def fetchall(self):
        return list(self)

    def __getattr__(self, key):
        return getattr(self.cursor, key)


class CountingConnection:
    def __init__(self, db):
        self.db = db
        self.rows = {'batches':0, 'values':0}

    def execute(self, sql, params=()):
        key = ('batches' if 'FROM observation_batches' in sql
               else 'values' if 'FROM observation_values' in sql else None)
        return CountingCursor(self.db.execute(sql,params),self.rows,key)

    def __getattr__(self, key):
        return getattr(self.db,key)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime.fromisoformat(AT)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


class StationResultChainTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.StationMonitoringNormalizationTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.database = self.fixture.database
        self.site = self.fixture.site_id
        self.endpoint = self.fixture.endpoint_id
        self.client = fixture_module.web_app.app.test_client()
        self.headers = self.fixture._headers('monitor-admin')
        self.samples = {}
        clock = mock.patch.object(core, '_utc_now', return_value=AT)
        clock.start(); self.addCleanup(clock.stop)
        api_clock = mock.patch.object(api, 'datetime', FrozenDateTime)
        api_clock.start(); self.addCleanup(api_clock.stop)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('DELETE FROM monitoring_factor_mappings')
            db.execute('UPDATE monitoring_endpoint_profiles SET instrument_asset_code=NULL,expected_interval_seconds=14400')
            db.execute('UPDATE monitoring_business_schedules SET result_delay_seconds=7200,tolerance_seconds=600')
            for cfg in FACTS['configurations']:
                db.execute('''INSERT INTO monitoring_factor_mappings
                    (endpoint_id,protocol_code,business_metric,instrument_asset_code,expected_interval_seconds,tolerance_seconds,effective_from)
                    VALUES (?,?,?,NULL,14400,600,?)''',
                    (self.endpoint,cfg['protocol_code'],cfg['business_metric'],cfg['mapping_effective_from']))
            self.fixture._set_expected_factors(db, [c['business_metric'] for c in FACTS['configurations']])
            for frame in FACTS['three_frame_chain']:
                raw = dict(frame['raw'], endpoint_id=self.endpoint, raw_frame=b'isolated-export')
                self.insert(db, 'ingest_raw_frames', raw)
                for group in frame['batches']:
                    batch = dict(group['batch'], endpoint_id=self.endpoint, business_site_id=self.site,
                                 idempotency_key='incident-'+str(group['batch']['id']))
                    self.insert(db, 'observation_batches', batch)
                    for value in group['values']:
                        self.insert(db, 'observation_values', value)
            latest = FACTS['latest_at_t0'][0]
            self.extra(db, 116020, latest['observed_at'], latest['business_metric'],
                       latest['standard_value'], latest['source_value_text'])
            db.execute('UPDATE ingest_raw_frames SET received_at=? WHERE id=116020', (latest['received_at'],))
            db.execute('UPDATE observation_batches SET received_at=?,normalized_at=? WHERE id=116020',
                       (latest['received_at'],latest['normalized_at']))
            db.commit()

        fixture_module.bootstrap_retention_state(self.database, now=datetime.fromisoformat(AT))

    @staticmethod
    def insert(db, table, record):
        columns = {r[1] for r in db.execute('PRAGMA table_info('+table+')')}
        values = {k:v for k,v in record.items() if k in columns}
        names = list(values)
        db.execute('INSERT INTO '+table+' ('+','.join(names)+') VALUES ('+','.join('?' for _ in names)+')',
                   [values[k] for k in names])

    def extra(self, db, identity, observed, metric, value, text, command=2011):
        raw = dict(FACTS['three_frame_chain'][0]['raw'],id=identity,endpoint_id=self.endpoint,
                   frame_sha256='incident-'+str(identity),raw_frame=b'isolated-export',received_at=AT)
        self.insert(db,'ingest_raw_frames',raw)
        batch = dict(FACTS['three_frame_chain'][0]['batches'][0]['batch'], id=identity,
                     raw_frame_id=identity,endpoint_id=self.endpoint,business_site_id=self.site,
                     observed_at=observed,received_at=AT,function_code=command,idempotency_key='extra-'+str(identity))
        self.insert(db,'observation_batches',batch)
        original = next(v for v in FACTS['three_frame_chain'][0]['batches'][0]['values'] if v['business_metric']==metric)
        self.insert(db,'observation_values',dict(original,id=identity,observation_batch_id=identity,
            standard_value=value,raw_value=value,raw_value_text=text))

    def get(self, suffix):
        path = '/api/station-monitoring/'+suffix
        response = self.client.get(path,headers=self.headers)
        self.assertEqual(response.status_code,200,response.get_json())
        self.samples[path] = response.get_json()
        return response.get_json()

    def project_results(self, db):
        db.row_factory = sqlite3.Row
        rows = db.execute('''SELECT value.*,batch.endpoint_id,batch.business_site_id,
            batch.observed_at,batch.received_at,batch.function_code
            FROM observation_values value JOIN observation_batches batch
            ON batch.id=value.observation_batch_id''').fetchall()
        for row in rows:
            core._project_business_observation(db,
                value_id=row['id'], batch_id=row['observation_batch_id'],
                endpoint_id=row['endpoint_id'], site_id=row['business_site_id'],
                protocol_code=row['protocol_code'], business_metric=row['business_metric'],
                instrument_asset_code=row['instrument_asset_code'], observed_at=row['observed_at'],
                received_at=row['received_at'], standard_value=row['standard_value'],
                standard_unit=row['standard_unit'], quality=row['quality'],
                function_code=row['function_code'],
                source_value_kind='avg' if row['function_code']==2061 else 'rtd')

    def test_actual_results_feed_consumers_with_business_identity_and_cycle_counts(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.extra(db,116022,'2026-10-22T06:10:00+00:00','ammonia',15,'15')
            self.project_results(db)
            rows = web_app._formal_business_observation_rows(
                db,'2026-10-22T06:00:00+00:00',AT,[self.site], 'ammonia')
            self.assertEqual([r['standard_value'] for r in rows],[0.105853,15])
            for row in rows:
                source = db.execute('SELECT source_observation_value_id FROM monitoring_business_observations WHERE id=?',
                                    (row['id'],)).fetchone()[0]
                self.assertNotEqual(source,row['id'])
            summary = web_app._formal_arrival_summary(
                db,'2026-10-21T16:00:00+00:00','2026-10-22T16:00:00+00:00',[self.site],evaluated_at=AT)
            ammonia = next(r for r in summary['by_metric'] if r['metric']=='ammonia')
            self.assertEqual(ammonia['actual_count'],1)
            self.assertLessEqual(ammonia['actual_count'],ammonia['expected_count'])
            db.execute("INSERT OR REPLACE INTO param_thresholds(metric,label,unit,low,high) VALUES ('ammonia','ammonia','mg/L',0,1)")
            with mock.patch.object(web_app,'datetime',FrozenDateTime):
                generated = web_app._generate_formal_data_reviews(db,'2026-10-22T06:00:00+00:00',metric='ammonia')
                self.assertEqual(generated['generated'],1)
                review = db.execute("SELECT * FROM data_reviews WHERE metric='ammonia'").fetchone()
                self.assertEqual(review['recorded_at'],'2026-10-22T06:10:00+00:00')
                self.assertEqual(review['monitoring_business_observation_id'],rows[-1]['id'])
                alerts = web_app._evaluate_formal_observation_alerts(db,'2026-10-22T06:00:00+00:00')
                self.assertGreaterEqual(alerts['triggered'],1)
                db.execute("UPDATE data_reviews SET status='archived'")
                db.execute("UPDATE alerts SET status='resolved'")
                before = db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]
                self.assertEqual(web_app._generate_formal_data_reviews(db,'2026-10-22T06:00:00+00:00')['generated'],0)
                self.assertEqual(web_app._evaluate_formal_observation_alerts(db,'2026-10-22T06:00:00+00:00'),
                                 {'evaluated':0,'triggered':0})
                self.assertEqual(db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0],before)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM alerts WHERE status!='resolved'").fetchone()[0],0)
                self.assertEqual(db.execute('SELECT status FROM data_reviews WHERE id=?',(review['id'],)).fetchone()[0],'archived')
                self.samples['downstream']={'arrival':summary,'review_source_business_id':rows[-1]['id'],
                    'review_recorded_at':review['recorded_at'],'first_alerts':alerts,
                    'repeat_alerts':{'evaluated':0,'triggered':0},'closed_items_preserved':True}
            db.commit()
        response=self.client.get('/api/data/health?period=custom&start=2026-10-22&end=2026-10-22',headers=self.headers)
        self.assertEqual(response.status_code,200,response.get_json())
        site=next(r for r in response.get_json()['by_site'] if r['site_id']==self.site)
        self.assertLessEqual(site['actual'],site['expected'])
        self.assertEqual(site['actual'],9)
        self.samples['health']=site

    def test_downstream_true_conflict_does_not_fall_back_and_observed_window_is_honest(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.extra(db,116022,'2026-10-22T08:00:00+00:00','ammonia',0.2,'0.2',2061)
            self.extra(db,116023,'2026-10-22T08:00:00+00:00','ammonia',0.3,'0.3',2061)
            self.extra(db,116024,'2026-10-22T08:00:00+00:00','ammonia',0.4,'0.4',2011)
            self.project_results(db)
            rows=web_app._formal_business_observation_rows(db,'2026-10-22T08:00:00+00:00',AT,[self.site],'ammonia')
            self.assertEqual(rows,[])
            # The 06:07 observation belongs to the 04:00 expected slot, not to 04:00 actual history.
            self.assertEqual(web_app._formal_business_observation_rows(
                db,'2026-10-22T04:00:00+00:00','2026-10-22T06:00:00+00:00',[self.site],'ammonia'),[])
            scheduled=web_app._formal_business_observation_rows(
                db,'2026-10-22T04:00:00+00:00','2026-10-22T06:00:00+00:00',[self.site],'ammonia',time_basis='scheduled')
            self.assertEqual(scheduled[0]['observed_at'],'2026-10-22T06:07:00+00:00')

    def test_sparse_production_facts_reach_latest_trend_list_and_detail(self):
        latest = self.get(f'sites/{self.site}/latest')['items']
        self.assertEqual(len(latest),9)
        by_metric = {v['business_metric']:v for v in latest}
        self.assertEqual((by_metric['ammonia']['standard_value_display'],by_metric['ammonia']['observed_at']),
                         ('0.105853','2026-10-22T06:07:00+00:00'))
        self.assertEqual(by_metric['codmn']['standard_value_display'],'2.2524')
        self.assertGreater(by_metric['total_phosphorus']['observed_at'],by_metric['ammonia']['observed_at'])
        trend = self.get(f'sites/{self.site}/trend?metric=ammonia&start=2026-10-22T06:00:00%2B00:00&end=2026-10-22T06:20:00%2B00:00')
        self.assertTrue(any(p.get('value_display')=='0.105853' for p in trend['points']))
        detail = self.get(f'sites/{self.site}/overview')
        self.assertEqual(len(detail['monitoring']['latest_values']),9)
        self.assertEqual(detail['monitoring']['latest_cycle']['formed_count'],9)
        self.assertEqual(detail['monitoring']['latest_cycle']['review_required_count'],0)
        self.get('sites?scope=all')
        self.get(f'sites/{self.site}/summary')
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            self.assertEqual(len(core.latest_values(db,self.site,at_time=AT)),9)
            self.assertEqual(core.latest_cycle(db,self.site,at_time=AT)['formed_count'],9)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observation_values').fetchone()[0],16)

    def test_late_resend_changes_at_different_times_and_real_same_time_conflict(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.extra(db,116021,'2026-10-22T05:00:00+00:00','ammonia',9.9,'9.9')
            self.extra(db,116022,'2026-10-22T06:10:00+00:00','ammonia',0.11,'0.110000')
            db.commit(); db.row_factory=sqlite3.Row
            value=next(v for v in core.latest_values(db,self.site,at_time=AT) if v['business_metric']=='ammonia')
            self.assertEqual((value['status'],value['standard_value_display']),('value','0.110000'))
            self.extra(db,116023,'2026-10-22T06:10:00+00:00','ammonia',0.12,'0.120000')
            db.commit()
            value=next(v for v in core.latest_values(db,self.site,at_time=AT) if v['business_metric']=='ammonia')
            self.assertEqual(value['status'],'review_required')
            self.assertIsNone(value['standard_value'])
            self.assertEqual(core.latest_cycle(db,self.site,at_time=AT)['review_required_count'],1)
        trend=self.get(f'sites/{self.site}/trend?metric=ammonia&start=2026-10-22T06:00:00%2B00:00&end=2026-10-22T06:20:00%2B00:00')
        self.assertTrue(any(p['status']=='review_required' for p in trend['points']))

    def test_newer_fault_and_missing_factor_are_not_hidden(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM observation_values WHERE business_metric='codmn'")
            self.extra(db,116021,'2026-10-22T09:00:00+00:00','ammonia',0.11,'0.11')
            db.execute('DELETE FROM observation_values WHERE id=116021')
            db.execute('''INSERT INTO monitoring_quality_issues
                (raw_frame_id,observation_batch_id,business_site_id,issue_type,object_summary,first_seen_at,last_seen_at)
                VALUES (116021,116021,?,'hj212_flag_fault','HJ212:w21003',?,?)''',(self.site,AT,AT))
            db.commit()
        summary=self.get(f'sites/{self.site}/summary')
        states={f['business_metric']:f for f in summary['axes']['data']['factors']}
        self.assertEqual(states['ammonia']['state'],'fault')
        self.assertIn(states['codmn']['state'],('missing','pending'))
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory=sqlite3.Row
            factors={f['business_metric']:f for f in core.latest_cycle(db,self.site,at_time=AT)['factors']}
            self.assertEqual(factors['ammonia']['reason_code'],'hj212_flag_fault')
            self.assertIn(factors['codmn']['status'],('missing','pending'))

    def test_bounded_batch_query_retains_factors_behind_256_empty_batches(self):
        with closing(sqlite3.connect(self.database)) as db:
            for index in range(10080):
                self.extra(db,200000+index,'2026-10-22T08:00:00+00:00','total_phosphorus',0.033024,'0.033024')
                db.execute('DELETE FROM observation_values WHERE id=?',(200000+index,))
            db.commit(); db.row_factory=sqlite3.Row
            queries=[];db.set_trace_callback(queries.append)
            counted=CountingConnection(db)
            started=time.perf_counter(); values=core.latest_values(counted,self.site,at_time=AT)
            elapsed=time.perf_counter()-started
            self.assertEqual(len(values),9)
            self.assertLess(elapsed,3)
            plan=db.execute('''EXPLAIN QUERY PLAN SELECT id FROM observation_batches
                INDEXED BY idx_observation_batch_site_observed
                WHERE business_site_id=? AND observed_at>=? AND observed_at<=?
                ORDER BY observed_at DESC,id DESC LIMIT ?''',
                (self.site,'2026-10-15T09:37:06+00:00',AT,core.LATEST_COLLECTION_BATCH_LIMIT)).fetchall()
            self.assertTrue(any('idx_observation_batch_site_observed' in r[3] for r in plan))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observation_values').fetchone()[0],16)
            self.samples['query_cost']={'batches':10084,'values':16,'elapsed_seconds':elapsed,'sql_statements':len(queries),
                'query_plan':[list(r) for r in plan],
                'actual_rows_read':counted.rows,
                'lookback_seconds':core.LATEST_COLLECTION_LOOKBACK_SECONDS,'batch_cap':core.LATEST_COLLECTION_BATCH_LIMIT}

    def test_dense_seven_day_history_stops_after_first_page_when_factors_found(self):
        with closing(sqlite3.connect(self.database)) as db:
            for index in range(10080):
                observed=(datetime.fromisoformat(AT)-timedelta(minutes=index+1)).replace(second=0).isoformat()
                self.extra(db,200000+index,observed,'total_phosphorus',0.033024,'0.033024')
            db.commit(); db.row_factory=sqlite3.Row
            counted=CountingConnection(db)
            started=time.perf_counter()
            values=core.latest_values(counted,self.site,at_time=AT)
            elapsed=time.perf_counter()-started
            self.assertEqual(len(values),9)
            self.assertLessEqual(counted.rows['batches'],256)
            self.assertLessEqual(counted.rows['values'],300)
            self.assertLess(elapsed,3)
            self.samples['dense_query_cost']={'stored_batches':10084,'stored_values':10096,
                'actual_rows_read':counted.rows,'elapsed_seconds':elapsed,
                'distribution':'synthetic minute phosphorus plus real sparse nine-factor facts'}

    def test_old_results_remain_visible_but_do_not_claim_current_health(self):
        later='2026-10-23T09:37:06+00:00'
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory=sqlite3.Row
            values=core.latest_values(db,self.site,at_time=later)
            self.assertEqual(len(values),9)
            self.assertTrue(all(v['observed_at']<later for v in values))
            cycle=core.latest_cycle(db,self.site,at_time=later)
            self.assertEqual(cycle['formed_count'],0)
            self.assertTrue(all(f['reason_code']=='stale_observation' for f in cycle['factors']))
            self.samples['stale_cycle']=cycle

    def test_historical_trend_survives_endpoint_replacement_without_cross_site_leakage(self):
        cutover='2026-10-22T09:00:00+00:00'
        start='2026-10-22T06:00:00+00:00'
        end='2026-10-22T08:30:00+00:00'
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory=sqlite3.Row
            def clone(table,row,**changes):
                record=dict(row); record.pop('id',None); record.update(changes)
                self.insert(db,table,record)
                return db.execute('SELECT last_insert_rowid()').fetchone()[0]
            before=core.result_trend_page(db,self.site,'ammonia',start,end)[0]
            self.assertEqual(len(before),1)
            original_profile=db.execute('SELECT * FROM monitoring_endpoint_profiles WHERE endpoint_id=?',(self.endpoint,)).fetchone()
            mappings=db.execute('SELECT * FROM monitoring_factor_mappings WHERE endpoint_id=?',(self.endpoint,)).fetchall()
            current=clone('trusted_endpoints',db.execute('SELECT * FROM trusted_endpoints WHERE id=?',
                (self.endpoint,)).fetchone(),station_code='9900000022')
            clone('monitoring_endpoint_profiles',original_profile,endpoint_id=current,effective_from=cutover)
            for mapping in mappings:
                clone('monitoring_factor_mappings',mapping,endpoint_id=current,effective_from=cutover)
            for schedule in db.execute('SELECT * FROM monitoring_business_schedules WHERE endpoint_id=?',(self.endpoint,)).fetchall():
                clone('monitoring_business_schedules',schedule,endpoint_id=current,effective_from=cutover)
            old_batch=db.execute('SELECT * FROM observation_batches ORDER BY observed_at,id LIMIT 1').fetchone()
            raw=clone('ingest_raw_frames',db.execute('SELECT * FROM ingest_raw_frames WHERE id=?',
                (old_batch['raw_frame_id'],)).fetchone(),endpoint_id=current,station_code='9900000022',
                frame_sha256='historical-cutover-fixture')
            batch=clone('observation_batches',old_batch,endpoint_id=current,raw_frame_id=raw,
                observed_at='2026-10-22T09:10:00+00:00',received_at=AT,idempotency_key='historical-cutover-fixture')
            for value in db.execute('SELECT * FROM observation_values WHERE observation_batch_id=?',(old_batch['id'],)).fetchall():
                clone('observation_values',value,observation_batch_id=batch)
            db.execute('UPDATE monitoring_endpoint_profiles SET effective_to=? WHERE endpoint_id=?',(cutover,self.endpoint))
            db.execute('UPDATE monitoring_factor_mappings SET effective_to=? WHERE endpoint_id=?',(cutover,self.endpoint))
            db.execute('UPDATE trusted_endpoints SET enabled=0 WHERE id=?',(self.endpoint,))
            db.commit()
            after=core.result_trend_page(db,self.site,'ammonia',start,end)[0]
            self.assertEqual(after,before)
            self.assertEqual(after[0]['value_display'],'0.105853')
            self.assertEqual(after[0]['observed_at'],'2026-10-22T06:07:00+00:00')
            configs=core.current_factor_configurations(db,self.site,at_time=AT)
            self.assertEqual(len(configs),9)
            self.assertEqual({c['endpoint_id'] for c in configs},{current})
            self.assertEqual(core.latest_cycle(db,self.site,at_time=AT)['expected_count'],9)
            self.assertEqual(len(core.latest_values(db,self.site,at_time=AT)),9)
            # Move the old device to another site, retaining the original site's historical profile.
            db.execute("UPDATE trusted_endpoints SET enabled=1,endpoint_state='bound',business_site_id=? WHERE id=?",
                       (self.fixture.other_site_id,self.endpoint))
            clone('monitoring_endpoint_profiles',original_profile,business_site_id=self.fixture.other_site_id,
                  effective_from=cutover)
            for mapping in mappings:
                clone('monitoring_factor_mappings',mapping,effective_from=cutover)
            db.commit()
            self.assertEqual(core.result_trend_page(db,self.site,'ammonia',start,end)[0],before)
            self.assertEqual(core.result_trend_page(db,self.fixture.other_site_id,'ammonia',start,end)[0],[])
            self.assertEqual(core.result_trend_page(db,self.fixture.other_site_id,'ammonia',start,AT)[0],[])
            self.assertEqual(db.execute('''SELECT COUNT(*) FROM observation_values value JOIN observation_batches batch
                ON batch.id=value.observation_batch_id WHERE batch.endpoint_id=?''',(self.endpoint,)).fetchone()[0],16)
        for site_id,expected_points in ((self.site,1),(self.fixture.other_site_id,0)):
            response=self.client.get(f'/api/station-monitoring/sites/{site_id}/trend?metric=ammonia&start={start.replace("+","%2B")}&end={end.replace("+","%2B")}',headers=self.headers)
            self.assertEqual(response.status_code,200,response.get_json())
            self.assertEqual(len(response.get_json()['points']),expected_points)

    def test_replaced_endpoint_only_contributes_current_nine_factors_without_deleting_history(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory=sqlite3.Row
            def clone(table, row, **changes):
                record=dict(row)
                record.pop('id',None)
                record.update(changes)
                self.insert(db,table,record)
                return db.execute('SELECT last_insert_rowid()').fetchone()[0]
            current=clone('trusted_endpoints',db.execute(
                'SELECT * FROM trusted_endpoints WHERE id=?',(self.endpoint,)).fetchone(),
                station_code='9900000022')
            for table in ('monitoring_endpoint_profiles','monitoring_factor_mappings','monitoring_business_schedules'):
                for row in db.execute('SELECT * FROM '+table+' WHERE endpoint_id=?',(self.endpoint,)).fetchall():
                    clone(table,row,endpoint_id=current)
            source_batch=db.execute('SELECT * FROM observation_batches ORDER BY observed_at,id LIMIT 1').fetchone()
            raw=clone('ingest_raw_frames',db.execute('SELECT * FROM ingest_raw_frames WHERE id=?',
                (source_batch['raw_frame_id'],)).fetchone(),endpoint_id=current,station_code='9900000022',
                frame_sha256='replacement-endpoint-facts')
            batch=clone('observation_batches',source_batch,endpoint_id=current,raw_frame_id=raw,
                        idempotency_key='replacement-endpoint-facts')
            for row in db.execute('SELECT * FROM observation_values WHERE observation_batch_id=?',
                                  (source_batch['id'],)).fetchall():
                clone('observation_values',row,observation_batch_id=batch)
            db.commit()
            self.assertEqual(len(core.current_factor_configurations(db,self.site,at_time=AT)),18)
            historical_sql='''SELECT value.* FROM observation_values value JOIN observation_batches batch
                ON batch.id=value.observation_batch_id WHERE batch.endpoint_id=? ORDER BY value.id'''
            historical=[tuple(row) for row in db.execute(historical_sql,(self.endpoint,))]
            self.assertEqual(len(historical),16)
            cases=[(0,'bound',self.site),(1,'disabled',self.site),
                   (1,'unbound',self.site),(1,'bound',self.fixture.other_site_id),
                   (1,'bound',None)]
            for enabled,state,owner in cases:
                with self.subTest(enabled=enabled,state=state,owner=owner):
                    db.execute('UPDATE trusted_endpoints SET enabled=?,endpoint_state=?,business_site_id=? WHERE id=?',
                               (enabled,state,owner,self.endpoint))
                    db.commit()
                    configs=core.current_factor_configurations(db,self.site,at_time=AT)
                    self.assertEqual(len(configs),9)
                    self.assertEqual({c['endpoint_id'] for c in configs},{current})
                    values,cycles=core.latest_values_and_cycles_batch(db,{self.site:configs},at_time=AT)
                    self.assertEqual(cycles[self.site]['expected_count'],9)
                    self.assertEqual(len(values[self.site]),9)
                    ammonia=next(v for v in values[self.site] if v['business_metric']=='ammonia')
                    self.assertEqual(ammonia['standard_value_display'],'0.105853')
                    self.assertEqual(ammonia['observed_at'],'2026-10-22T06:07:00+00:00')
                    self.assertEqual([tuple(row) for row in db.execute(historical_sql,(self.endpoint,))],historical)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM monitoring_factor_mappings WHERE endpoint_id=?',
                                               (self.endpoint,)).fetchone()[0],9)
            self.samples['replacement_endpoint']={'before_active_factors':18,'after_active_factors':9,
                'expected_count':9,'current_values':9,'old_values_preserved':16,
                'old_mappings_preserved':9,'cases':cases}
            with mock.patch.object(web_app, 'datetime', FrozenDateTime):
                current_summary=web_app._formal_arrival_summary(db,'2026-10-22T04:00:00+00:00',
                    '2026-10-23T00:00:00+00:00',[self.site],evaluated_at=AT)
            self.assertEqual(sum(r['expected_count'] for r in current_summary['by_metric']),9)
            self.samples['replacement_endpoint']['current_period_expected']=9

    def test_historical_arrival_counts_original_site_cycles_after_rebinding(self):
        self.test_historical_trend_survives_endpoint_replacement_without_cross_site_leakage()
        start='2026-10-22T04:00:00+00:00'
        end='2026-10-22T08:30:00+00:00'
        with closing(sqlite3.connect(self.database)) as db:
            self.project_results(db)
            before=db.execute('SELECT COUNT(*) FROM monitoring_business_observations').fetchone()[0]
            original=web_app._formal_arrival_summary(db,start,end,[self.site],evaluated_at=end)
            self.assertEqual(sum(r['expected_count'] for r in original['by_metric']),9)
            self.assertEqual(sum(r['actual_count'] for r in original['by_metric']),9)
            self.assertEqual(original['total_avg'],100)
            self.assertTrue(all(r['actual_count']<=r['expected_count'] for r in original['by_metric']))
            other=web_app._formal_arrival_summary(db,start,end,[self.fixture.other_site_id],evaluated_at=end)
            self.assertEqual(sum(r['actual_count'] for r in other['by_metric']),0)
            self.assertEqual(len(core.current_factor_configurations(db,self.site,at_time=AT)),9)
            across=web_app._formal_arrival_summary(db,start,AT,[self.site],evaluated_at=AT)
            self.assertTrue(all(r['actual_count']<=r['expected_count'] for r in across['by_metric']))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM monitoring_business_observations').fetchone()[0],before)
            self.samples['historical_arrival']={'original':original,'new_site':other,
                'cross_switch':across,'current_factors':9,'business_rows':before}

    def test_historical_health_keeps_sparse_cycle_counts_when_endpoint_is_disabled(self):
        # Shift this isolated sample back one day so the real day-based HTTP API
        # evaluates a closed historical day, without mocking API time boundaries.
        with closing(sqlite3.connect(self.database)) as db:
            for row in db.execute('SELECT id,observed_at,received_at FROM observation_batches').fetchall():
                db.execute('UPDATE observation_batches SET observed_at=?,received_at=? WHERE id=?',
                    ((datetime.fromisoformat(row[1])-timedelta(days=1)).isoformat(),
                     (datetime.fromisoformat(row[2])-timedelta(days=1)).isoformat(),row[0]))
            self.project_results(db)
            db.execute('UPDATE monitoring_endpoint_profiles SET effective_to=? WHERE endpoint_id=?',
                       ('2026-10-22T09:00:00+00:00',self.endpoint))
            db.commit()
        url='/api/data/health?period=custom&start=2026-10-21&end=2026-10-21'
        before=self.client.get(url,headers=self.headers)
        self.assertEqual(before.status_code,200,before.get_json())
        old=next(r for r in before.get_json()['by_site'] if r['site_id']==self.site)
        self.assertEqual(old['expected'],54)
        self.assertEqual(old['actual'],9)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE trusted_endpoints SET enabled=0 WHERE id=?',(self.endpoint,))
            db.commit()
        after=self.client.get(url,headers=self.headers)
        self.assertEqual(after.status_code,200,after.get_json())
        new=next(r for r in after.get_json()['by_site'] if r['site_id']==self.site)
        self.assertEqual(new,old)
        self.assertLessEqual(new['actual'],new['expected'])
        other=next(r for r in after.get_json()['by_site'] if r['site_id']==self.fixture.other_site_id)
        self.assertEqual(other['actual'],0)
        self.samples['historical_health']={'before_disabled':old,'after_disabled':new}


class SavedSelectionResultTest(unittest.TestCase):
    """Real raw7591285 CP; only the authenticated envelope is synthetic."""
    METRICS = ['ph', 'water_temp', 'dissolved_oxygen', 'conductivity', 'turbidity',
               'codmn', 'ammonia', 'total_phosphorus', 'total_nitrogen']
    CP = ('DataTime=20261022164800;001-Rtd=7.198,001-Flag=N;'
          '004-Rtd=23.960,004-Flag=N;005-Rtd=7.754,005-Flag=N;'
          '006-Rtd=184.460,006-Flag=N;016-Rtd=1.805,016-Flag=N;'
          '022-Rtd=0.000,022-Flag=N;027-Rtd=0.000,027-Flag=N;'
          '029-Rtd=0.000,029-Flag=N;030-Rtd=0.000,030-Flag=N;'
          '060-Rtd=0.032,060-Flag=N;065-Rtd=1.117,065-Flag=N;'
          '081-Rtd=0.001,081-Flag=N;101-Rtd=0.041,101-Flag=N;'
          '153-Rtd=2.918,153-Flag=N;274-Rtd=1027870.000,274-Flag=N')

    def setUp(self):
        from backend import test_station_expected_factors as expected_fixture
        self.fixture = expected_fixture.StationExpectedFactorsTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.database = self.fixture.database
        self.site = self.fixture.site_id
        self.endpoint, station, password, _, server = self.fixture.install_bound_hj212_endpoint()
        body = (f'QN=20261022181505842;ST=21;CN=2011;PW={password};MN={station};'
                f'Flag=9;CP=&&{self.CP}&&').encode('ascii')
        frame = (b'##' + f'{len(body):04d}'.encode('ascii') + body
                 + expected_fixture.hj212_crc(body).encode('ascii') + b'\r\n')
        accepted = server._process_raw_result(frame, '2026-10-22T10:17:06+00:00')
        self.assertTrue(accepted.keep_connection)
        with closing(sqlite3.connect(self.database)) as db:
            raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
        self.assertEqual(core.normalize_raw_frame(self.database, raw_id), 'partial')
        self.version = 0
        self.headers = self.fixture.headers('expected-admin')

    def save(self, metrics):
        response = self.fixture.client.put(
            f'/api/station-monitoring/sites/{self.site}/expected-factors',
            headers=self.headers, json={'metrics': metrics, 'version': self.version,
                                      'idempotency_key': f'save-reread-{self.version}'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.version = response.get_json()['version']
        return response.get_json()

    def read(self, expected):
        # A new connection after the actual PUT, not a patched in-memory configuration.
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            values = core.latest_values(db, self.site)
            self.assertEqual({v['business_metric'] for v in values}, set(expected))
            self.assertTrue(all(v['observed_at']=='2026-10-22T08:48:00+00:00' for v in values))
            for metric in self.METRICS:
                points, _, _ = core.result_trend_page(
                    db, self.site, metric, '2026-10-22T08:00:00+00:00',
                    '2026-10-22T09:00:00+00:00')
                self.assertEqual(len(points), int(metric in expected), metric)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observation_values WHERE '
                "quality='valid' AND is_current=1 AND is_published=1").fetchone()[0], 14)
        response = self.fixture.client.get(
            f'/api/station-monitoring/sites/{self.site}/latest', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({v['business_metric'] for v in response.get_json()['items']}, set(expected))
        trend = self.fixture.client.get(
            f'/api/station-monitoring/sites/{self.site}/trend', headers=self.headers,
            query_string={'metric': expected[0], 'start': '2026-10-22T08:00:00+00:00',
                          'end': '2026-10-22T09:00:00+00:00'})
        self.assertEqual(trend.status_code, 200, trend.get_json())
        self.assertEqual(len(trend.get_json()['points']), 1)
        return values

    def test_original_ingress_save_nine_reopen_latest_and_trend(self):
        saved = self.save(self.METRICS)
        self.assertGreater(saved['effective_from'], '2026-10-22T08:48:00+00:00')
        values = self.read(self.METRICS)
        temperature = next(v for v in values if v['business_metric']=='water_temp')
        self.assertEqual(temperature['standard_value_display'], '23.960')
        with closing(sqlite3.connect(self.database)) as db:
            for table in ('monitoring_endpoint_profiles', 'monitoring_factor_mappings',
                          'monitoring_business_schedules'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_explicit_mapping_boundary_and_rebound_site_remain_isolated(self):
        self.save(self.METRICS)
        self.read(self.METRICS)
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            db.execute("""INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,
                timezone,enabled,expected_granularity,effective_from)
                VALUES (?,?,'Asia/Shanghai',1,'periodic','2020-01-01T00:00:00+00:00')""",
                (self.endpoint, self.site))
            db.execute("""INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,
                business_metric,effective_from,enabled)
                VALUES (?,'HJ212:001','ph','2026-10-22T09:00:00+00:00',1)""", (self.endpoint,))
            db.commit()
            self.assertNotIn('ph', {v['business_metric'] for v in core.latest_values(db, self.site)})
            self.assertEqual(core.result_trend_page(db, self.site, 'ph',
                '2026-10-22T08:00:00+00:00', '2026-10-22T10:00:00+00:00')[0], [])
            db.execute("UPDATE monitoring_factor_mappings SET effective_from='2020-01-01T00:00:00+00:00'")
            self.assertIn('ph', {v['business_metric'] for v in core.latest_values(db, self.site)})
            db.execute('UPDATE trusted_endpoints SET business_site_id=? WHERE id=?',
                       (self.fixture.other_site_id, self.endpoint))
            db.commit()
        response = self.fixture.client.put(
            f'/api/station-monitoring/sites/{self.fixture.other_site_id}/expected-factors',
            headers=self.headers, json={'metrics': self.METRICS, 'version': 0,
                                      'idempotency_key': 'new-site-selection'})
        self.assertEqual(response.status_code, 200)
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            self.assertEqual(core.latest_values(db, self.fixture.other_site_id), [])
            self.assertEqual(core.latest_values(db, self.site), [])
            for metric in self.METRICS:
                self.assertEqual(core.result_trend_page(db, self.fixture.other_site_id, metric,
                    '2026-10-22T08:00:00+00:00', '2026-10-22T10:00:00+00:00')[0], [])

    def test_add_cancel_and_reselect_keeps_results_without_backdating_slots(self):
        self.save(['ph'])
        self.read(['ph'])
        self.save(['ph', 'water_temp'])
        self.read(['ph', 'water_temp'])
        self.save(['water_temp'])
        self.read(['water_temp'])
        saved = self.save(['ph', 'water_temp'])
        self.read(['ph', 'water_temp'])
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            db.execute("""INSERT INTO monitoring_business_schedules(endpoint_id,protocol_code,
                timezone,interval_seconds,anchor_local_time,tolerance_seconds,effective_from)
                VALUES (?,NULL,'Asia/Shanghai',14400,'00:00:00',600,
                '2020-01-01T00:00:00+00:00')""", (self.endpoint,))
            configs = core.current_factor_configurations(db, self.site)
            for config in configs:
                self.assertEqual(core.expected_business_slots(db, config,
                    '2026-10-22T00:00:00+00:00', saved['effective_from']), [])
            self.assertEqual(core._historical_factor_configurations(
                db, self.site, '2026-10-22T09:00:00+00:00'), [])


if __name__ == '__main__':
    unittest.main()
