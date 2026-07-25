import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from operations_baseline import build_baseline


class OperationsBaselineTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE insp_plan_items (id INTEGER, plan_id INTEGER, result TEXT, execution_status TEXT, completed_at TEXT);
            CREATE TABLE work_orders (id INTEGER, status TEXT, created_at TEXT, closed_at TEXT);
            CREATE TABLE alerts (id INTEGER, status TEXT, created_at TEXT, resolved_at TEXT);
            CREATE TABLE data_reviews (id INTEGER, status TEXT, created_at TEXT, reviewed_at TEXT);
            CREATE TABLE analytics_events (event_id TEXT, event_name TEXT, occurred_at TEXT, context_json TEXT);
        ''')
        self.db.executemany('INSERT INTO insp_plan_items VALUES (?,?,?,?,?)', [
            (1, 1, 'normal', 'active', '2026-07-20 08:00:00'),
            (2, 1, None, 'active', None),
            (3, 1, 'normal', 'cancelled', '2026-07-20 08:00:00'),
        ])
        self.db.executemany('INSERT INTO work_orders VALUES (?,?,?,?)', [
            (1, 'closed', '2026-07-20 08:00:00', '2026-07-20 10:00:00'),
            (2, 'in_progress', '2026-07-20 08:00:00', None),
        ])
        self.db.executemany('INSERT INTO analytics_events VALUES (?,?,?,?)', [
            ('old', 'inspection.item.queued', '2026-06-01 08:00:00', None),
            ('new-queued', 'inspection.item.queued', '2026-07-20 08:00:00', None),
            ('new-synced', 'inspection.item.synced', '2026-07-20 08:05:00', None),
        ])

    def tearDown(self):
        self.db.close()

    def test_uses_active_planned_items_and_filters_events_to_selected_period(self):
        baseline = build_baseline(self.db, '2026-07-01 00:00:00', '2026-08-01 00:00:00')
        coverage = baseline['north_star']['inspection_coverage']
        self.assertEqual(coverage['numerator'], 1)
        self.assertEqual(coverage['denominator'], 2)
        self.assertEqual(coverage['value'], 50.0)
        self.assertEqual(baseline['frontline']['offline_closure_success_rate']['value'], 100.0)
        self.assertEqual(baseline['frontline']['offline_closure_success_rate']['denominator'], 1)

    def test_collection_metrics_compute_from_pc_events(self):
        import json
        self.db.executemany(
            'INSERT INTO analytics_events (event_id, event_name, occurred_at, context_json) VALUES (?,?,?,?)',
            [
                # 审核耗时：opened 08:00:00 -> submitted 08:02:30 = 150s
                ('rev-1-open', 'review.opened', '2026-07-20 08:00:00', json.dumps({'review_id': 'R1', 'source_type': 'inspection'})),
                ('rev-1-sub', 'review.submitted', '2026-07-20 08:02:30', json.dumps({'review_id': 'R1', 'source_type': 'inspection', 'decision': 'approve'})),
                # 报表自助：打开2次，导出1次 -> 50%
                ('rep-open-1', 'report.opened', '2026-07-21 09:00:00', '{}'),
                ('rep-open-2', 'report.opened', '2026-07-22 09:00:00', '{}'),
                ('rep-exp-1', 'report.exported', '2026-07-22 09:05:00', json.dumps({'report_type': 'evaluation'})),
                # 行动队列：进入1次
                ('aq-1', 'action_queue.entered', '2026-07-22 10:00:00', json.dumps({'queue_key': 'workorders'})),
            ],
        )
        baseline = build_baseline(self.db, '2026-07-01 00:00:00', '2026-08-01 00:00:00')
        coll = baseline['collection']
        self.assertEqual(coll['review_duration']['state'], 'ready')
        self.assertEqual(coll['review_duration']['value'], 150.0)
        self.assertEqual(coll['review_duration']['samples'], 1)
        self.assertEqual(coll['report_self_service_rate']['state'], 'ready')
        self.assertEqual(coll['report_self_service_rate']['value'], 50.0)
        self.assertEqual(coll['action_queue_decision_rate']['state'], 'ready')
        self.assertEqual(coll['action_queue_decision_rate']['value'], 1)

    def test_collection_metrics_stay_collecting_without_events(self):
        baseline = build_baseline(self.db, '2026-07-01 00:00:00', '2026-08-01 00:00:00')
        coll = baseline['collection']
        self.assertEqual(coll['review_duration']['state'], 'collecting')
        self.assertEqual(coll['report_self_service_rate']['state'], 'collecting')
        self.assertEqual(coll['action_queue_decision_rate']['state'], 'collecting')


if __name__ == '__main__':
    unittest.main()
