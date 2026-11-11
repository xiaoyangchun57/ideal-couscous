import sqlite3
import unittest
from calendar import monthrange
from datetime import datetime

import app
from photo_quality import current_photo_quality, photo_quality_deductions
from test_photo_quality_reuse import PhotoQualityReuseTest


class HealthSummaryQueryTest(unittest.TestCase):
    def test_preferred_sources_and_conflicts_match_detail_query(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript('''
            CREATE TABLE observation_batches (id INTEGER PRIMARY KEY, is_current INTEGER,
                projection_state TEXT, function_code INTEGER);
            CREATE TABLE observation_values (id INTEGER PRIMARY KEY, is_current INTEGER,
                is_published INTEGER, quality TEXT);
            CREATE TABLE monitoring_business_observations (id INTEGER PRIMARY KEY,
                business_site_id INTEGER, endpoint_id INTEGER, business_metric TEXT,
                instrument_asset_code TEXT, protocol_code TEXT, standard_value REAL,
                standard_unit TEXT, quality TEXT, scheduled_at TEXT, observed_at TEXT,
                received_at TEXT, timeliness TEXT, projection_version INTEGER,
                observation_batch_id INTEGER, source_observation_value_id INTEGER,
                is_current INTEGER);
        ''')
        samples = [
            (1, 2061, 'HJ212:a', 2.0, '2026-11-01T00:00:00'),
            (2, 2061, 'SL651:a', 2.0, '2026-11-01T00:00:00'),
            (3, 2051, 'SL651:a', 3.0, '2026-11-01T00:00:00'),
            (4, 2061, 'HJ212:a', 2.0, '2026-11-02T00:00:00'),
            (5, 2061, 'SL651:a', 3.0, '2026-11-02T00:00:00'),
            (6, 2051, 'HJ212:a', 4.0, '2026-11-03T00:00:00'),
            (7, 2051, 'SL651:a', 4.0, '2026-11-03T00:00:00'),
        ]
        for ident, function, protocol, value, stamp in samples:
            db.execute('INSERT INTO observation_batches VALUES (?,1,?,?)', (ident, 'completed', function))
            db.execute('INSERT INTO observation_values VALUES (?,1,1,?)', (ident, 'valid'))
            db.execute('INSERT INTO monitoring_business_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (ident, 1, 2, 'ph', None, protocol, value, 'mg/L', 'valid', stamp,
                        stamp, stamp, 'on_time', 1, ident, ident, 1))
        start, end = '2026-11-01', '2026-12-01'
        old = app._formal_business_observation_rows(db, start, end, [1], time_basis='scheduled')
        new = app._formal_health_observation_rows(db, start, end, [1])
        fields = ('business_site_id', 'endpoint_id', 'business_metric', 'instrument_asset_code',
                  'standard_value', 'scheduled_at', 'observed_at')
        normalize = lambda rows: sorted(tuple(row[field] for field in fields) for row in rows)
        self.assertEqual(normalize(new), normalize(old))
        self.assertEqual(len(new), 2)


class PhotoSummaryQueryTest(unittest.TestCase):
    def test_current_month_multiple_people_and_site_scope(self):
        fixture = PhotoQualityReuseTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        db = fixture.db
        now = datetime.now()
        month = now.strftime('%Y-%m')
        stamp = f'{month}-01 12:00:00'
        start = f'{month}-01 00:00:00'
        end = f'{month}-{monthrange(now.year, now.month)[1]:02d} 23:59:59'
        for ident, person, site in ((1, 8, 1), (2, 9, 2), (3, 8, 2)):
            db.execute('INSERT INTO insp_plans VALUES (?,NULL,?,\'completed\',?,NULL)', (ident, stamp[:10], person))
            db.execute('INSERT INTO insp_plan_items (id,plan_id,site_id,item_name,completed_at) VALUES (?,?,?,?,?)',
                       (ident, ident, site, 'Meter', stamp))
            db.execute('''INSERT INTO operation_attachments
                (id,source_type,source_id,item_id,plan_id,site_id,uploader_id)
                VALUES (?,'inspection',?,?,?,?,?)''', (ident, ident, ident, ident, site, person))
            db.execute('''INSERT INTO photo_quality_evaluations
                (attachment_id,status,reviewed_at,responsible_user_id,inspection_completed_at,
                 score_month,attribution_status) VALUES (?,'unqualified',?,?,?,?, 'reliable')''',
                       (ident, stamp, person, stamp, month))
        db.execute('''INSERT INTO photo_quality_evaluations
            (attachment_id,status,reviewed_at,responsible_user_id,inspection_completed_at,
             score_month,attribution_status) VALUES (3,'qualified',?,8,?,?,'reliable')''',
                   (stamp, stamp, month))
        for sites, expected in ((None, {8: 1, 9: 1}), ([1], {8: 1}), ([2], {9: 1}), ([], {})):
            batch = photo_quality_deductions(db, [8, 9], start, end, sites)
            old = {person: total for person in (8, 9)
                   if (total := sum(row['deduction'] for row in current_photo_quality(db, person, start, end, sites)))}
            self.assertEqual(batch, old)
            self.assertEqual(batch, expected)

    def test_batch_matches_detail_after_rejudgment_and_scope(self):
        fixture = PhotoQualityReuseTest('test_photo_rejudgment_keeps_field_fact_and_original_score_month')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.test_photo_rejudgment_keeps_field_fact_and_original_score_month()
        db = fixture.db
        start, end = '2026-09-01 00:00:00', '2026-09-30 23:59:59'
        for sites in (None, [1], [2], []):
            old = sum(item['deduction'] for item in current_photo_quality(db, 8, start, end, allowed_site_ids=sites))
            self.assertEqual(photo_quality_deductions(db, [8], start, end, sites).get(8, 0), old)
        db.execute("DELETE FROM photo_quality_evaluations WHERE status='qualified'")
        self.assertEqual(photo_quality_deductions(db, [8], start, end, [1]), {8: 1})
        self.assertEqual(photo_quality_deductions(db, [8], start, end, [2]), {})

        for attachment_id in range(5000, 5200):
            db.execute('''INSERT INTO operation_attachments
                (id,source_type,source_id,item_id,plan_id,site_id,uploader_id,review_status)
                VALUES (?,'inspection',3001,3001,301,1,8,'rejected')''', (attachment_id,))
            db.execute('''INSERT INTO photo_quality_evaluations
                (attachment_id,status,reviewed_at,responsible_user_id,inspection_completed_at,
                 score_month,attribution_status)
                VALUES (?,'unqualified','2026-10-02',8,'2026-09-30 23:30:00',
                        '2026-09','reliable')''', (attachment_id,))
        statements = []
        db.set_trace_callback(statements.append)
        try:
            old = sum(item['deduction'] for item in current_photo_quality(db, 8, start, end))
            old_count = len(statements)
            statements.clear()
            new = photo_quality_deductions(db, [8], start, end).get(8, 0)
            new_count = len(statements)
        finally:
            db.set_trace_callback(None)
        self.assertEqual((old, new), (201, 201))
        self.assertGreater(old_count, 400)
        self.assertLess(new_count, 15)


if __name__ == '__main__':
    unittest.main()
