import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import contextmanager

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class AttachmentEvidenceQualificationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'evidence.db')
        self.upload_dir = os.path.join(self.temp_dir, 'uploads')
        os.makedirs(self.upload_dir)
        self.original_get_db = app_module.get_db
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.original_tokens = dict(app_module._tokens)
        self.original_site_cache = dict(app_module._site_ids_cache)

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
        app_module.UPLOAD_DIR = self.upload_dir
        app_module._tokens.clear()
        app_module._tokens['admin-token'] = {
            'id': 1, 'role': 'admin', 'username': 'admin', 'real_name': '管理员',
        }
        app_module._site_ids_cache.clear()
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, real_name TEXT, role TEXT);
                INSERT INTO users VALUES (1,'admin','管理员','admin');
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, gps_lat REAL, gps_lng REAL);
                INSERT INTO sites VALUES (1,'资格测试站',28.071303,115.539684);
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_name TEXT, status TEXT, assignee_id INTEGER
                );
                INSERT INTO insp_plans VALUES (20,'资格测试计划','active',2);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, item_name TEXT,
                    result TEXT, check_in_time TEXT, check_out_time TEXT, completed_at TEXT,
                    check_time TEXT, required_photos INTEGER DEFAULT 1,
                    execution_status TEXT DEFAULT 'active'
                );
                INSERT INTO insp_plan_items VALUES
                    (10,20,1,'浊度仪检查',NULL,'2026-08-12 10:00:00','2026-08-12 11:00:00',NULL,NULL,1,'active');
                CREATE TABLE inspection_checkins (site_id INTEGER, user_id INTEGER, check_time TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER, status TEXT,
                    check_in_time TEXT, review_submitted_at TEXT, resolved_at TEXT
                );
                INSERT INTO work_orders VALUES
                    (30,'WO-EVIDENCE-1',1,'in_progress','2026-08-12 10:00:00',NULL,NULL);
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, stored_path TEXT,
                    file_type TEXT DEFAULT 'image', mime_type TEXT DEFAULT 'image/jpeg',
                    file_size INTEGER DEFAULT 1, description TEXT DEFAULT '', source_type TEXT,
                    source_id INTEGER, plan_id INTEGER, item_id INTEGER, item_name TEXT DEFAULT '',
                    site_id INTEGER, uploader_id INTEGER, uploader_name TEXT DEFAULT '',
                    gps_lat REAL, gps_lng REAL, taken_at TEXT, created_at TEXT,
                    category TEXT DEFAULT '', is_deleted INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
                    review_required INTEGER DEFAULT 1, review_status TEXT DEFAULT 'pending',
                    is_flagged INTEGER DEFAULT 0, flag_reason TEXT DEFAULT '', capture_source TEXT DEFAULT '',
                    duplicate_of_id INTEGER, watermark_text TEXT DEFAULT '', extra_json TEXT DEFAULT '{}',
                    evidence_qualification TEXT DEFAULT 'review', evidence_basis TEXT DEFAULT 'unknown',
                    evidence_reason TEXT DEFAULT '', evidence_next_action TEXT DEFAULT '',
                    evidence_evaluated_at TEXT, evidence_evaluation_id INTEGER
                );
                CREATE TABLE attachment_evidence_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, attachment_id INTEGER,
                    qualification TEXT, basis TEXT, verified_taken_at TEXT, reason TEXT,
                    next_action TEXT, facts_json TEXT, evaluation_type TEXT, rule_version TEXT,
                    actor_id INTEGER, actor_name TEXT, replaces_evaluation_id INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE evidence_quality_remediations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, attachment_id INTEGER,
                    business_type TEXT, business_id TEXT, status TEXT DEFAULT 'open', reason TEXT,
                    due_at TEXT, resolution TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT, UNIQUE(attachment_id,business_type,business_id)
                );
                CREATE TABLE photo_capture_sessions (
                    id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE, user_id INTEGER, site_id INTEGER,
                    plan_id INTEGER, item_id INTEGER, work_order_id INTEGER, issued_at TEXT,
                    expires_at TEXT, used_at TEXT, attachment_id INTEGER
                );
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module.UPLOAD_DIR = self.original_upload_dir
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        shutil.rmtree(self.temp_dir)

    @staticmethod
    def headers():
        return {'Authorization': 'Bearer admin-token'}

    def assess(self, **overrides):
        values = {
            'source_type': 'inspection', 'source_id': 10, 'site_id': 1, 'uploader_id': 2,
            'received_at': '2026-08-12 10:10:00', 'capture_source': 'watermark_album',
            'gps_lat': 28.071303, 'gps_lng': 115.539684,
        }
        values.update(overrides)
        with app_module.get_db() as db:
            return app_module._assess_attachment_evidence(db, **values)

    def test_source_matrix_and_client_spoofing(self):
        missing = self.assess(client_taken_at='2026-08-12 10:05:00')
        self.assertEqual((missing['qualification'], missing['basis']), ('ineligible', 'unknown'))

        exif = self.assess(exif_taken_at='2026-08-12 10:05:00')
        self.assertEqual((exif['qualification'], exif['basis']), ('qualified', 'exif'))

        complete_watermark = self.assess(
            watermark_fields={'taken_at': '2026-08-12 10:05:00', 'code': 'ANTI123456'},
            watermark_confidence=0.91, watermark_status='recognized')
        self.assertEqual((complete_watermark['qualification'], complete_watermark['basis']),
                         ('qualified', 'watermark'))

        weak_watermark = self.assess(
            watermark_fields={'taken_at': '2026-08-12 10:05:00'},
            watermark_confidence=0.91, watermark_status='recognized')
        self.assertEqual(weak_watermark['qualification'], 'ineligible')

        consistent = self.assess(
            exif_taken_at='2026-08-12 10:05:00',
            watermark_fields={'taken_at': '2026-08-12 10:09:00', 'code': 'ANTI123456'},
            watermark_confidence=0.91, watermark_status='recognized')
        self.assertEqual((consistent['qualification'], consistent['basis']),
                         ('qualified', 'exif_watermark'))

        conflict = self.assess(
            exif_taken_at='2026-08-12 10:01:00',
            watermark_fields={'taken_at': '2026-08-12 10:09:00', 'code': 'ANTI123456'},
            watermark_confidence=0.91, watermark_status='recognized')
        self.assertEqual((conflict['qualification'], conflict['basis']),
                         ('ineligible', 'time_conflict'))

    def test_evidence_site_geofence_300m_boundary_without_mutation(self):
        for distance, qualification in ((299.9, 'qualified'), (300, 'qualified'),
                                        (300.01, 'ineligible'), (450, 'ineligible'), (None, 'ineligible')):
            with self.subTest(distance=distance), mock.patch.object(app_module, '_haversine', return_value=distance):
                assessment = self.assess(exif_taken_at='2026-08-12 10:05:00')
                self.assertEqual(assessment['qualification'], qualification)
                with app_module.get_db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM attachment_evidence_evaluations').fetchone()[0], 0)

    def test_archive_name_keeps_one_business_name_and_times_remain_separate(self):
        with app_module.get_db() as db:
            presentation = app_module._attachment_presentation(db, {
                'id': 99, 'filename': 'missing-time.jpg', 'stored_path': '/uploads/missing-time.jpg',
                'source_type': 'inspection', 'source_id': 10, 'item_id': 10, 'plan_id': 20,
                'item_name': '浊度仪检查', 'site_id': 1, 'site_name': '资格测试站',
                'category': '现场照片', 'taken_at': None, 'created_at': '2026-08-12 10:10:00',
            })
        self.assertEqual(presentation['archive_name'], '浊度仪检查')
        self.assertNotIn('拍摄时间待确认', presentation['archive_name'])
        self.assertNotIn('2026-08-12 10:10:00', presentation['archive_name'])

    def test_time_window_session_location_duplicate_and_delay_rules(self):
        future = self.assess(exif_taken_at='2026-08-12 10:16:00')
        self.assertEqual(future['qualification'], 'ineligible')
        self.assertIn('晚于服务器接收时间', future['reason'])

        before_checkin = self.assess(exif_taken_at='2026-08-12 09:54:00')
        self.assertEqual(before_checkin['qualification'], 'ineligible')
        self.assertIn('早于到站签到', before_checkin['reason'])

        session = self.assess(
            capture_source='camera', client_taken_at='2026-01-01 00:00:00',
            session={'id': 8, 'issued_at': '2026-08-12 10:04:00'})
        self.assertEqual((session['qualification'], session['basis'], session['taken_at']),
                         ('qualified', 'camera_session', '2026-08-12 10:04:00'))

        no_location = self.assess(exif_taken_at='2026-08-12 10:05:00', gps_lat=None, gps_lng=None)
        self.assertEqual(no_location['qualification'], 'review')

        duplicate = self.assess(exif_taken_at='2026-08-12 10:05:00', duplicate_of_id=99)
        self.assertEqual((duplicate['qualification'], duplicate['basis']), ('ineligible', 'duplicate'))

        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items SET check_in_time='2026-08-10 08:00:00',
                check_out_time='2026-08-12 11:00:00' WHERE id=10""")
        delayed = self.assess(exif_taken_at='2026-08-11 09:00:00')
        self.assertEqual(delayed['qualification'], 'review')
        self.assertIn('超过24小时', delayed['reason'])

    def test_capture_session_is_reserved_once(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO photo_capture_sessions
                VALUES (1,?,2,1,20,10,NULL,'2026-08-12 10:00:00',
                        '2026-08-12 10:15:00',NULL,NULL)""",
                (app_module._hash_token('one-time-token'),))
            found = app_module._capture_session_row(
                db, 'one-time-token', user_id=2, site_id=1, plan_id=20, item_id=10,
                received_at=app_module._parse_dt('2026-08-12 10:05:00'))
            first = app_module._reserve_capture_session(db, found, '2026-08-12 10:05:00')
            second = app_module._reserve_capture_session(db, found, '2026-08-12 10:05:01')
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_historical_scan_appends_internal_assessment_without_new_remediation(self):
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal' WHERE id=10")
            db.execute("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,plan_id,item_id,item_name,site_id,
                 uploader_id,review_status,created_at)
                VALUES (40,'missing.jpg','/uploads/missing.jpg','inspection',10,20,10,
                        '浊度仪检查',1,2,'approved','2026-08-12 10:10:00')""")
        result = app_module.migrate_attachment_evidence_qualification()
        self.assertEqual(result, {'evaluated': 1, 'remediations': 0})
        with app_module.get_db() as db:
            attachment = db.execute("""SELECT review_status,evidence_qualification,taken_at,
                evidence_evaluation_id FROM operation_attachments WHERE id=40""").fetchone()
            remediation_count = db.execute(
                'SELECT COUNT(*) FROM evidence_quality_remediations WHERE attachment_id=40').fetchone()[0]
        self.assertEqual((attachment['review_status'], attachment['evidence_qualification'], attachment['taken_at']),
                         ('approved', 'ineligible', None))
        self.assertIsNotNone(attachment['evidence_evaluation_id'])
        self.assertEqual(remediation_count, 0)
        self.assertEqual(app_module.migrate_attachment_evidence_qualification(),
                         {'evaluated': 0, 'remediations': 0})

    def test_current_archive_only_returns_approved_qualified_formal_rows(self):
        with open(os.path.join(self.upload_dir, 'valid.jpg'), 'wb') as handle:
            handle.write(b'valid current evidence')
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,site_id,uploader_id,review_status,
                 taken_at,created_at,evidence_qualification,evidence_basis,evidence_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                (51,'valid.jpg','/uploads/valid.jpg','inspection',10,1,2,'approved',
                 '2026-08-10 09:00:00','2026-08-12 09:00:00','qualified','exif','通过'),
                (52,'missing.jpg','/uploads/missing2.jpg','inspection',10,1,2,'approved',
                 None,'2026-08-11 09:00:00','ineligible','unknown','缺失'),
                (53,'conflict.jpg','/uploads/conflict.jpg','inspection',10,1,2,'pending',
                 None,'2026-08-12 09:00:00','ineligible','time_conflict','冲突'),
                (54,'rejected.jpg','/uploads/rejected.jpg','inspection',10,1,2,'rejected',
                 None,'2026-08-12 09:01:00','ineligible','unknown','已驳回'),
                (55,'voided.jpg','/uploads/voided.jpg','inspection',10,1,2,'voided',
                 None,'2026-08-12 09:02:00','ineligible','unknown','已作废'),
            ])
            db.execute("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,site_id,uploader_id,review_status,
                 taken_at,created_at,evidence_qualification,evidence_basis,evidence_reason,extra_json)
                VALUES (56,'supplement.jpg','/uploads/supplement.jpg','inspection',10,1,2,'approved',
                 '2026-08-10 09:00:00','2026-08-12 09:00:00','qualified','exif','通过',
                 '{"material_role":"supplement"}')""")
            db.executemany("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,plan_id,item_id,site_id,uploader_id,
                 review_status,taken_at,created_at,evidence_qualification,evidence_basis,evidence_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                (57,'orphan.jpg','/uploads/orphan.jpg','inspection',999,20,10,1,2,
                 'approved','2026-08-10 09:10:00','2026-08-12 09:10:00','qualified','exif','通过'),
                (58,'cross-site.jpg','/uploads/cross-site.jpg','inspection',10,20,10,2,2,
                 'approved','2026-08-10 09:20:00','2026-08-12 09:20:00','qualified','exif','通过'),
            ])
        capture = self.client.get('/api/attachments?current_archive=1&date_from=2026-08-10&date_to=2026-08-10',
                                  headers=self.headers())
        self.assertEqual(capture.status_code, 200, capture.json)
        self.assertEqual((capture.json['total'], capture.json['evidence_issue_count']), (1, 0))
        self.assertEqual([row['id'] for row in capture.json['items']], [51])
        history = self.client.get('/api/attachments?history_archive=1&include_voided=1',
                                  headers=self.headers())
        self.assertTrue({57, 58}.issubset({row['id'] for row in history.json['items']}))

    def test_actionable_evidence_issue_excludes_terminal_and_supplement_rows(self):
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,site_id,uploader_id,review_status,
                 created_at,evidence_qualification,evidence_reason,extra_json,is_deleted)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                (81,'actionable.jpg','/uploads/actionable.jpg','inspection',10,1,2,'pending',
                 '2026-08-12 11:00:00','ineligible','缺少可信拍摄时间','{}',0),
                (82,'superseded.jpg','/uploads/old.jpg','inspection',10,1,2,'superseded',
                 '2026-08-12 11:01:00','ineligible','已被替换','{}',0),
                (83,'supplement.jpg','/uploads/supplement-pending.jpg','inspection',10,1,2,'pending',
                 '2026-08-12 11:02:00','ineligible','补充材料',
                 '{"material_role":"supplement"}',0),
                (84,'deleted.jpg','/uploads/deleted-issue.jpg','inspection',10,1,2,'pending',
                 '2026-08-12 11:03:00','ineligible','已移出','{}',1),
            ])

        listing = self.client.get('/api/attachments?evidence_issue=1', headers=self.headers())
        stats = self.client.get('/api/attachments/stats', headers=self.headers())
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertEqual(stats.status_code, 200, stats.json)
        self.assertEqual([row['id'] for row in listing.json['items']], [81])
        self.assertEqual(listing.json['evidence_issue_count'], 1)
        self.assertEqual(stats.json['evidence_issues'], 1)

    def test_workorder_current_archive_requires_real_same_site_order(self):
        with open(os.path.join(self.upload_dir, 'workorder.jpg'), 'wb') as handle:
            handle.write(b'valid work order evidence')
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,site_id,uploader_id,review_status,
                 taken_at,created_at,evidence_qualification,evidence_basis,evidence_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                (61,'workorder.jpg','/uploads/workorder.jpg','workorder',30,1,2,'approved',
                 '2026-08-10 10:00:00','2026-08-12 10:00:00','qualified','exif','通过'),
                (62,'missing-order.jpg','/uploads/missing-order.jpg','workorder',999,1,2,'approved',
                 '2026-08-10 10:01:00','2026-08-12 10:01:00','qualified','exif','通过'),
                (63,'cross-order.jpg','/uploads/cross-order.jpg','workorder',30,2,2,'approved',
                 '2026-08-10 10:02:00','2026-08-12 10:02:00','qualified','exif','通过'),
            ])

        current = self.client.get('/api/attachments?current_archive=1', headers=self.headers())
        history = self.client.get('/api/attachments?history_archive=1&include_voided=1',
                                  headers=self.headers())
        stats = self.client.get('/api/attachments/stats', headers=self.headers())
        self.assertEqual([row['id'] for row in current.json['items']], [61])
        self.assertEqual({row['id'] for row in history.json['items']}, {62, 63})
        self.assertEqual(stats.json['total'], current.json['total'])

    def test_business_history_keeps_rows_hidden_from_current_archive(self):
        with open(os.path.join(self.upload_dir, 'current.jpg'), 'wb') as handle:
            handle.write(b'valid current evidence')
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,item_id,item_name,site_id,uploader_id,
                 review_status,taken_at,created_at,evidence_qualification,evidence_basis,extra_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                (71,'current.jpg','/uploads/current.jpg','inspection',10,10,'浊度仪检查',1,2,
                 'approved','2026-08-12 10:05:00','2026-08-12 10:06:00','qualified','camera_session','{}'),
                (72,'rejected.jpg','/uploads/rejected-history.jpg','inspection',10,10,'浊度仪检查',1,2,
                 'rejected','2026-08-12 10:07:00','2026-08-12 10:08:00','qualified','camera_session','{}'),
                (73,'voided.jpg','/uploads/voided-history.jpg','inspection',10,10,'浊度仪检查',1,2,
                 'voided','2026-08-12 10:09:00','2026-08-12 10:10:00','qualified','camera_session','{}'),
                (74,'supplement.jpg','/uploads/supplement-history.jpg','inspection',10,10,'浊度仪检查',1,2,
                 'pending',None,'2026-08-12 10:11:00','ineligible','unknown',
                 '{"material_role":"supplement"}'),
                (75,'null-status.jpg','/uploads/null-status.jpg','inspection',10,10,'浊度仪检查',1,2,
                 None,None,'2026-08-12 10:12:00','review','unknown','{}'),
                (76,'superseded.jpg','/uploads/superseded.jpg','inspection',10,10,'浊度仪检查',1,2,
                 'superseded','2026-08-12 10:13:00','2026-08-12 10:14:00','qualified','camera_session','{}'),
            ])
            db.execute("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,item_id,item_name,site_id,uploader_id,
                 review_status,taken_at,created_at,evidence_qualification,evidence_basis,extra_json,is_deleted)
                VALUES (77,'deleted.jpg','/uploads/deleted.jpg','inspection',10,10,'浊度仪检查',1,2,
                 'approved','2026-08-12 10:15:00','2026-08-12 10:16:00','qualified','camera_session','{}',1)""")

            item = db.execute('SELECT * FROM insp_plan_items WHERE id=10').fetchone()
            history, effective = app_module._item_attachment_history(db, item)

        self.assertEqual([row['id'] for row in history], [71, 72, 73, 74, 75, 76])
        self.assertEqual([row['id'] for row in effective], [71])

        archive = self.client.get('/api/attachments?current_archive=1', headers=self.headers())
        self.assertEqual(archive.status_code, 200, archive.json)
        self.assertEqual([row['id'] for row in archive.json['items']], [71])

        history_archive = self.client.get(
            '/api/attachments?history_archive=1&include_voided=1', headers=self.headers())
        self.assertEqual(history_archive.status_code, 200, history_archive.json)
        self.assertEqual({row['id'] for row in history_archive.json['items']}, {72, 73, 74, 75, 76, 77})
        current_ids = {row['id'] for row in archive.json['items']}
        history_ids = {row['id'] for row in history_archive.json['items']}
        self.assertTrue(current_ids.isdisjoint(history_ids))
        self.assertEqual(current_ids | history_ids, {71, 72, 73, 74, 75, 76, 77})

    def test_manual_review_writes_are_retired_without_side_effects(self):
        image_path = os.path.join(self.upload_dir, 'manual.jpg')
        Image.new('RGB', (16, 16), 'white').save(image_path, format='JPEG')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,site_id,uploader_id,gps_lat,gps_lng,
                 review_status,created_at,evidence_qualification)
                VALUES (60,'manual.jpg','/uploads/manual.jpg','inspection',10,1,2,28.071303,
                        115.539684,'approved','2026-08-12 10:10:00','ineligible')""")
            initial = {
                'qualification': 'ineligible', 'basis': 'unknown', 'taken_at': None,
                'reason': '无可信依据', 'next_action': '补传原图', 'facts': {},
            }
            app_module._record_attachment_evaluation(db, 60, initial, evaluation_type='historical_scan')

        before = None
        with app_module.get_db() as db:
            before = db.execute('SELECT COUNT(*) FROM attachment_evidence_evaluations').fetchone()[0]
        retired = self.client.post('/api/attachments/60/evidence-manual-review',
            headers=self.headers(), json={
                'basis': 'exif', 'taken_at': '2026-08-12 10:05:00',
                'reference': '客户端声称有EXIF', 'reason': '测试',
            })
        self.assertEqual(retired.status_code, 410, retired.json)
        self.assertEqual(retired.json['code'], 'EVIDENCE_MANUAL_REVIEW_RETIRED')
        revoked = self.client.post('/api/attachments/60/evidence-manual-review/revoke',
            headers=self.headers(), json={'reason': '复核发现水印并非原图内容'})
        self.assertEqual(revoked.status_code, 410, revoked.json)
        with app_module.get_db() as db:
            after = db.execute('SELECT COUNT(*) FROM attachment_evidence_evaluations').fetchone()[0]
            remediation_count = db.execute('SELECT COUNT(*) FROM evidence_quality_remediations').fetchone()[0]
        self.assertEqual((before, after, remediation_count), (1, 1, 0))
        history = self.client.get('/api/attachments/60/evidence-evaluations', headers=self.headers())
        self.assertEqual(history.status_code, 200, history.json)
        self.assertEqual([row['evaluation_type'] for row in history.json], ['historical_scan'])


if __name__ == '__main__':
    unittest.main()
