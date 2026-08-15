import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


def jpeg_bytes(color='white'):
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), color).save(buffer, format='JPEG')
    return buffer.getvalue()


class MobilePhotoProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.upload_dir = os.path.join(self.temp_dir, 'uploads')
        self.original_get_db = app_module.get_db
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.original_tokens = dict(app_module._tokens)
        self.original_recognize = app_module._recognize_watermark

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
        app_module._tokens['operator-token'] = {
            'id': 2, 'role': 'operator', 'real_name': '现场运维', 'username': 'operator',
        }
        now = datetime.now()
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, gps_lat REAL, gps_lng REAL);
                INSERT INTO sites VALUES (1,'测试站',28.071303,115.539684);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                INSERT INTO user_sites VALUES (2,1);
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                INSERT INTO plan_schedules VALUES (5,'approved');
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, status TEXT,
                    plan_schedule_id INTEGER, start_date TEXT, plan_date TEXT
                );
                INSERT INTO insp_plans VALUES (10,2,'active',5,NULL,NULL);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, item_name TEXT,
                    category TEXT, result TEXT, check_in_time TEXT, check_out_time TEXT,
                    completed_at TEXT, check_time TEXT, review_status INTEGER DEFAULT 0,
                    evidence_status TEXT DEFAULT '', rework_required_at TEXT DEFAULT '',
                    execution_status TEXT DEFAULT 'active', required_photos INTEGER DEFAULT 0,
                    actual_photos INTEGER DEFAULT 0, photo_urls TEXT DEFAULT '[]'
                );
                INSERT INTO insp_plan_items
                    (id,plan_id,site_id,item_name,category,result,check_in_time,check_out_time,
                     completed_at,check_time)
                VALUES (100,10,1,'浊度仪表读数','设备检查',NULL,NULL,NULL,NULL,NULL);
                CREATE TABLE inspection_checkins (
                    id INTEGER PRIMARY KEY, site_id INTEGER, user_id INTEGER, check_time TEXT
                );
                CREATE TABLE photo_requirements (
                    id INTEGER PRIMARY KEY, item_name TEXT, review_required INTEGER,
                    watermark_keyword TEXT, category TEXT
                );
                CREATE TABLE mobile_idempotency (
                    idempotency_key TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT, created_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, stored_path TEXT,
                    file_type TEXT, mime_type TEXT, file_size INTEGER, description TEXT,
                    source_type TEXT, source_id INTEGER, plan_id INTEGER, item_id INTEGER,
                    item_name TEXT DEFAULT '', site_id INTEGER, uploader_id INTEGER,
                    uploader_name TEXT, gps_lat REAL, gps_lng REAL, taken_at TEXT,
                    created_at TEXT, category TEXT, capture_source TEXT, sha256_hash TEXT,
                    duplicate_of_id INTEGER, perceptual_hash TEXT DEFAULT '', watermark_code TEXT DEFAULT '',
                    watermark_text TEXT DEFAULT '', recognized_category TEXT DEFAULT '',
                    match_status TEXT DEFAULT 'manual', match_confidence REAL,
                    review_required INTEGER DEFAULT 0, requirement_id INTEGER,
                    review_status TEXT DEFAULT 'pending', extra_json TEXT DEFAULT '{}',
                    is_deleted INTEGER DEFAULT 0, is_flagged INTEGER DEFAULT 0,
                    flag_reason TEXT DEFAULT '', flag_rule TEXT DEFAULT '',
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
                CREATE TABLE photo_capture_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT UNIQUE, user_id INTEGER,
                    site_id INTEGER, plan_id INTEGER, item_id INTEGER, work_order_id INTEGER,
                    issued_at TEXT, expires_at TEXT, used_at TEXT, attachment_id INTEGER,
                    gps_lat REAL, gps_lng REAL, distance_m REAL,
                    rework_required_at TEXT, capture_source TEXT DEFAULT ''
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT,
                    source_id INTEGER, is_read INTEGER DEFAULT 0
                );
            ''')
            db.execute('INSERT INTO inspection_checkins VALUES (1,1,2,?)',
                       (now.strftime('%Y-%m-%d %H:%M:%S'),))
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module.UPLOAD_DIR = self.original_upload_dir
        app_module._recognize_watermark = self.original_recognize
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        shutil.rmtree(self.temp_dir)

    @staticmethod
    def headers():
        return {'Authorization': 'Bearer operator-token'}

    def upload(self, image, **metadata):
        payload = {
            'site_id': 1,
            'image': 'data:image/jpeg;base64,' + base64.b64encode(image).decode('ascii'),
            'plan_id': 10, 'item_id': 100, 'item_name': '客户端伪造名称',
            **metadata,
        }
        return self.client.post('/api/mobile/upload-site-photo', headers=self.headers(), json=payload)

    def set_valid_watermark(self):
        now = datetime.now().strftime('%Y.%m.%d %H:%M')
        app_module._recognize_watermark = lambda _: {
            'text': f'时间:{now}\n经纬度:28.071303 N,115.539684 E\n防伪WRWYCRY1K14X34',
            'confidence': 0.99,
            'status': 'recognized',
        }

    def set_retake_required(self, item_id=100):
        required_at = (datetime.now() - timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')
        checkout_at = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET result='normal', review_status=3, evidence_status='supplement_required',
                    rework_required_at=?, check_out_time=? WHERE id=?""",
                (required_at, checkout_at, item_id))
        return required_at, checkout_at

    def create_capture_session(self, *, item_id=100, capture_source='camera',
                               gps_lat=28.071303, gps_lng=115.539684):
        return self.client.post('/api/mobile/photo-capture-session', headers=self.headers(), json={
            'site_id': 1, 'plan_id': 10, 'item_id': item_id,
            'capture_source': capture_source, 'gps_lat': gps_lat, 'gps_lng': gps_lng,
        })

    def assert_no_upload_artifacts(self):
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM attachment_evidence_evaluations').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM mobile_idempotency').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM notifications').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0], 0)
        photo_dir = os.path.join(self.upload_dir, 'site_photos')
        self.assertFalse(os.path.isdir(photo_dir) and os.listdir(photo_dir))

    def test_unreadable_album_does_not_create_business_attachment(self):
        app_module._recognize_watermark = lambda _: {
            'text': '', 'confidence': None, 'status': 'unreadable',
        }
        response = self.upload(jpeg_bytes(), capture_source='watermark_album',
                               _idempotency_key='album-unreadable')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['accepted_for_review'])
        self.assertTrue(response.json['can_keep_as_supplement'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)

    def test_explicit_retention_creates_supplement_only(self):
        app_module._recognize_watermark = lambda _: {
            'text': '', 'confidence': None, 'status': 'unreadable',
        }
        response = self.upload(jpeg_bytes('gray'), capture_source='watermark_album',
                               keep_as_supplement=True, _idempotency_key='album-supplement')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['supplemental'])
        self.assertFalse(response.json['accepted_for_review'])
        with app_module.get_db() as db:
            row = db.execute('SELECT review_required,extra_json FROM operation_attachments').fetchone()
            self.assertEqual(row['review_required'], 0)
            self.assertEqual(json.loads(row['extra_json'])['material_role'], 'supplement')
            self.assertEqual(app_module._qualified_evidence_count(db, 'inspection', 100), 0)

    def test_valid_watermark_album_enters_review_and_binds_server_item(self):
        self.set_valid_watermark()
        response = self.upload(jpeg_bytes('orange'), capture_source='watermark_album',
                               _idempotency_key='album-qualified')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['accepted_for_review'])
        with app_module.get_db() as db:
            row = db.execute('SELECT * FROM operation_attachments').fetchone()
        self.assertEqual((row['source_type'], row['source_id'], row['review_status']),
                         ('inspection', 100, 'pending'))
        self.assertEqual(json.loads(row['extra_json'])['item_name'], '浊度仪表读数')

    def test_duplicate_and_idempotent_replay_do_not_create_more_formal_rows(self):
        self.set_valid_watermark()
        image = jpeg_bytes('green')
        first = self.upload(image, capture_source='watermark_album', _idempotency_key='first')
        replay = self.upload(image, capture_source='watermark_album', _idempotency_key='first')
        duplicate = self.upload(image, capture_source='watermark_album', _idempotency_key='second')
        self.assertTrue(first.json['accepted_for_review'])
        self.assertEqual(replay.json['id'], first.json['id'])
        self.assertFalse(duplicate.json['accepted_for_review'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 1)

    def test_client_camera_claim_without_server_session_is_rejected(self):
        response = self.upload(jpeg_bytes('red'), capture_source='camera',
                               taken_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                               gps_lat=28.071303, gps_lng=115.539684)
        self.assertFalse(response.json['accepted_for_review'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)

    def test_location_verified_one_time_camera_session(self):
        session = self.client.post('/api/mobile/photo-capture-session', headers=self.headers(), json={
            'site_id': 1, 'plan_id': 10, 'item_id': 100,
            'gps_lat': 28.071303, 'gps_lng': 115.539684,
        })
        self.assertEqual(session.status_code, 200, session.json)
        metadata = {
            'capture_source': 'camera', 'capture_session': session.json['capture_session'],
            'taken_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'gps_lat': 28.071303, 'gps_lng': 115.539684,
        }
        accepted = self.upload(jpeg_bytes('blue'), _idempotency_key='camera-1', **metadata)
        reused = self.upload(jpeg_bytes('purple'), _idempotency_key='camera-2', **metadata)
        self.assertTrue(accepted.json['accepted_for_review'])
        self.assertFalse(reused.json['accepted_for_review'])
        self.assertEqual(reused.json['code'], 'PHOTO_SOURCE_NOT_ACCEPTED')

    def test_return_retake_after_checkout_uses_item_session_without_new_checkin(self):
        required_at, checkout_at = self.set_retake_required()
        with app_module.get_db() as db:
            db.execute('DELETE FROM inspection_checkins')
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        self.assertTrue(session.json['retake'])

        accepted = self.upload(
            jpeg_bytes('navy'), capture_source='camera',
            capture_session=session.json['capture_session'], rework_required_at=required_at,
            taken_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            gps_lat=28.071303, gps_lng=115.539684,
            _idempotency_key='return-camera',
        )
        self.assertEqual(accepted.status_code, 200, accepted.json)
        self.assertTrue(accepted.json['accepted_for_review'])
        with app_module.get_db() as db:
            item = db.execute(
                'SELECT check_out_time,rework_required_at FROM insp_plan_items WHERE id=100'
            ).fetchone()
            attachment = db.execute(
                'SELECT id,evidence_qualification FROM operation_attachments'
            ).fetchone()
            capture = db.execute(
                'SELECT used_at,attachment_id,rework_required_at FROM photo_capture_sessions'
            ).fetchone()
            checkin_count = db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0]
        self.assertEqual((item['check_out_time'], item['rework_required_at']),
                         (checkout_at, required_at))
        self.assertEqual(checkin_count, 0)
        self.assertEqual(attachment['evidence_qualification'], 'qualified')
        self.assertTrue(capture['used_at'])
        self.assertEqual(capture['attachment_id'], attachment['id'])
        self.assertEqual(capture['rework_required_at'], required_at)

    def test_return_retake_accepts_qualified_watermark_without_session_and_submits(self):
        required_at, checkout_at = self.set_retake_required()
        self.set_valid_watermark()
        uploaded = self.upload(
            jpeg_bytes('gold'), capture_source='watermark_album',
            _idempotency_key='return-watermark-qualified',
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        self.assertTrue(uploaded.json['accepted_for_review'])

        submitted = self.client.post('/api/mobile/submit-item', headers=self.headers(), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps([uploaded.json['url']]),
            '_idempotency_key': 'submit-return-watermark-qualified',
        })
        self.assertEqual(submitted.status_code, 200, submitted.json)
        with app_module.get_db() as db:
            item = db.execute("""SELECT review_status,evidence_status,rework_required_at,
                check_out_time,photo_urls FROM insp_plan_items WHERE id=100""").fetchone()
            attachment = db.execute('SELECT * FROM operation_attachments').fetchone()
            evaluation = db.execute(
                'SELECT * FROM attachment_evidence_evaluations WHERE id=?',
                (attachment['evidence_evaluation_id'],),
            ).fetchone()
            session_count = db.execute('SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0]
        facts = json.loads(evaluation['facts_json'])
        self.assertEqual(session_count, 0)
        self.assertEqual((item['review_status'], item['evidence_status']), (1, 'replacement_submitted'))
        self.assertEqual((item['rework_required_at'], item['check_out_time']),
                         (required_at, checkout_at))
        self.assertEqual(json.loads(item['photo_urls']), [uploaded.json['url']])
        self.assertEqual((attachment['capture_source'], attachment['review_status']),
                         ('watermark_album', 'pending'))
        self.assertEqual(facts['rework_required_at'], required_at)
        self.assertLessEqual(facts['distance_m'], 500)

    def test_capture_session_is_camera_only_and_camera_requires_current_location(self):
        self.set_retake_required()
        missing = self.client.post(
            '/api/mobile/photo-capture-session', headers=self.headers(), json={
                'site_id': 1, 'plan_id': 10, 'item_id': 100, 'capture_source': 'camera',
            })
        self.assertEqual(missing.status_code, 409, missing.json)
        self.assertEqual(missing.json['code'], 'CAPTURE_LOCATION_REQUIRED')
        remote = self.create_capture_session(capture_source='camera', gps_lat=30.0, gps_lng=116.0)
        self.assertEqual(remote.status_code, 409, remote.json)
        self.assertEqual(remote.json['code'], 'CAPTURE_LOCATION_OUT_OF_RANGE')
        album = self.create_capture_session(capture_source='watermark_album')
        self.assertEqual(album.status_code, 400, album.json)
        self.assertEqual(album.json['code'], 'CAPTURE_SESSION_CAMERA_ONLY')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0], 0)

    def test_capture_session_rejects_non_finite_and_out_of_range_coordinates(self):
        invalid_coordinates = (
            ('nan', 115.539684), ('inf', 115.539684), ('not-a-number', 115.539684),
            (91, 115.539684), (-91, 115.539684),
            (28.071303, 181), (28.071303, -181),
        )
        for index, (lat, lng) in enumerate(invalid_coordinates):
            with self.subTest(lat=lat, lng=lng):
                response = self.create_capture_session(gps_lat=lat, gps_lng=lng)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(response.json['code'], 'CAPTURE_LOCATION_INVALID')
                with app_module.get_db() as db:
                    self.assertEqual(db.execute(
                        'SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0], 0)

        self.assertEqual(app_module._parse_gps_pair(-90, -180), (-90.0, -180.0))
        self.assertEqual(app_module._parse_gps_pair(90, 180), (90.0, 180.0))
        self.assertGreater(app_module._haversine(-90, -180, 90, 180), 0)
        self.assertIsNone(app_module._haversine('nan', 0, 0, 0))

    def test_replacement_submitted_camera_session_keeps_current_rework_cycle(self):
        required_at, _ = self.set_retake_required()
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET review_status=1,evidence_status='replacement_submitted' WHERE id=100""")
        response = self.create_capture_session()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['retake'])
        with app_module.get_db() as db:
            session = db.execute("""SELECT rework_required_at,capture_source
                FROM photo_capture_sessions""").fetchone()
        self.assertEqual((session['rework_required_at'], session['capture_source']),
                         (required_at, 'camera'))

    def test_exif_and_watermark_coordinate_conflicts_never_enter_review(self):
        required_at, _ = self.set_retake_required()
        now = datetime.now().strftime('%Y.%m.%d %H:%M')
        scenarios = (
            ('exif-target-watermark-wrong', (28.071303, 115.539684), (30.0, 116.0)),
            ('watermark-target-exif-wrong', (30.0, 116.0), (28.071303, 115.539684)),
        )
        for index, (label, exif_coords, watermark_coords) in enumerate(scenarios):
            with self.subTest(label=label):
                app_module._recognize_watermark = lambda _, coords=watermark_coords, suffix=index: {
                    'text': (f'时间:{now}\n经纬度:{coords[0]:.6f} N,{coords[1]:.6f} E\n'
                             f'防伪GPSCONFLICT{suffix}9'),
                    'confidence': 0.99,
                    'status': 'recognized',
                }
                with mock.patch.object(app_module, '_extract_image_capture_metadata', return_value={
                    'taken_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'gps_lat': exif_coords[0], 'gps_lng': exif_coords[1],
                    'metadata_source': 'exif',
                }):
                    response = self.upload(
                        jpeg_bytes(('navy', 'gold')[index]), capture_source='watermark_album',
                        _idempotency_key=f'gps-conflict-{index}',
                    )
                self.assertEqual(response.status_code, 200, response.json)
                self.assertFalse(response.json['accepted_for_review'])
                self.assertFalse(response.json['can_keep_as_supplement'])
                self.assertIn('位置冲突', response.json['reason'])
        with app_module.get_db() as db:
            item = db.execute("""SELECT review_status,evidence_status,rework_required_at,
                photo_urls FROM insp_plan_items WHERE id=100""").fetchone()
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
        self.assertEqual((item['review_status'], item['evidence_status'], item['rework_required_at']),
                         (3, 'supplement_required', required_at))
        self.assertEqual(json.loads(item['photo_urls']), [])

    def test_invalid_embedded_coordinates_fail_closed(self):
        now = datetime.now().strftime('%Y.%m.%d %H:%M')
        app_module._recognize_watermark = lambda _: {
            'text': f'时间:{now}\n防伪INVALIDGPS123',
            'confidence': 0.99,
            'status': 'recognized',
        }
        with mock.patch.object(app_module, '_extract_image_capture_metadata', return_value={
            'taken_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'gps_lat': float('nan'), 'gps_lng': 115.539684,
            'metadata_source': 'exif',
        }):
            response = self.upload(
                jpeg_bytes('coral'), capture_source='watermark_album',
                _idempotency_key='invalid-embedded-gps',
            )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['accepted_for_review'])
        self.assertFalse(response.json['can_keep_as_supplement'])
        self.assertIn('经纬度', response.json['reason'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)

    def test_watermark_retake_missing_time_can_only_be_kept_as_supplement(self):
        required_at, _ = self.set_retake_required()
        app_module._recognize_watermark = lambda _: {
            'text': '', 'confidence': None, 'status': 'unreadable',
        }
        response = self.upload(
            jpeg_bytes('teal'), capture_source='watermark_album',
            capture_session='client-fake-session', rework_required_at=required_at,
            _idempotency_key='retake-album-no-time',
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['accepted_for_review'])
        self.assertEqual(response.json['code'], 'PHOTO_SOURCE_NOT_ACCEPTED')
        self.assertTrue(response.json['can_keep_as_supplement'])
        self.assertIn('拍摄时间', response.json['reason'])

        retained = self.upload(
            jpeg_bytes('teal'), capture_source='watermark_album',
            capture_session='client-fake-session', rework_required_at=required_at,
            keep_as_supplement=True, _idempotency_key='retake-album-no-time:supplement',
        )
        self.assertEqual(retained.status_code, 200, retained.json)
        self.assertTrue(retained.json['supplemental'])
        self.assertFalse(retained.json['accepted_for_review'])
        with app_module.get_db() as db:
            attachment = db.execute('SELECT review_required,extra_json FROM operation_attachments').fetchone()
            item = db.execute("""SELECT review_status,evidence_status,rework_required_at,
                check_out_time,photo_urls FROM insp_plan_items WHERE id=100""").fetchone()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0], 0)
        self.assertEqual(attachment['review_required'], 0)
        self.assertEqual(json.loads(attachment['extra_json'])['material_role'], 'supplement')
        self.assertEqual((item['review_status'], item['evidence_status'], item['rework_required_at']),
                         (3, 'supplement_required', required_at))
        self.assertEqual(json.loads(item['photo_urls']), [])

    def test_watermark_retake_missing_location_is_not_formal_evidence(self):
        required_at, _ = self.set_retake_required()
        taken_at = datetime.now().strftime('%Y.%m.%d %H:%M')
        app_module._recognize_watermark = lambda _: {
            'text': f'时间:{taken_at}\n防伪ANTINOLOCATION123',
            'confidence': 0.99,
            'status': 'recognized',
        }
        response = self.upload(
            jpeg_bytes('silver'), capture_source='watermark_album',
            _idempotency_key='retake-album-no-location',
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['accepted_for_review'])
        self.assertTrue(response.json['can_keep_as_supplement'])
        self.assertIn('位置', response.json['reason'])
        with app_module.get_db() as db:
            item = db.execute(
                'SELECT review_status,evidence_status,rework_required_at,photo_urls '
                'FROM insp_plan_items WHERE id=100').fetchone()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
        self.assertEqual((item['review_status'], item['evidence_status'], item['rework_required_at']),
                         (3, 'supplement_required', required_at))
        self.assertEqual(json.loads(item['photo_urls']), [])

    def test_watermark_retake_from_previous_rejection_cycle_cannot_submit(self):
        first_cycle, _ = self.set_retake_required()
        self.set_valid_watermark()
        uploaded = self.upload(
            jpeg_bytes('khaki'), capture_source='watermark_album',
            _idempotency_key='retake-watermark-old-cycle',
        )
        self.assertTrue(uploaded.json['accepted_for_review'])
        second_cycle = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with app_module.get_db() as db:
            db.execute('UPDATE insp_plan_items SET rework_required_at=? WHERE id=100',
                       (second_cycle,))
            before_item = tuple(db.execute("""SELECT review_status,evidence_status,
                rework_required_at,photo_urls,actual_photos FROM insp_plan_items WHERE id=100""").fetchone())
            before_attachment = tuple(db.execute("""SELECT review_status,evidence_qualification,
                evidence_evaluation_id FROM operation_attachments WHERE id=?""",
                (uploaded.json['id'],)).fetchone())

        submitted = self.client.post('/api/mobile/submit-item', headers=self.headers(), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps([uploaded.json['url']]),
            '_idempotency_key': 'submit-retake-watermark-old-cycle',
        })
        self.assertEqual(submitted.status_code, 409, submitted.json)
        self.assertEqual(submitted.json['code'], 'RETAKE_EVIDENCE_CYCLE_MISMATCH')
        with app_module.get_db() as db:
            after_item = tuple(db.execute("""SELECT review_status,evidence_status,
                rework_required_at,photo_urls,actual_photos FROM insp_plan_items WHERE id=100""").fetchone())
            after_attachment = tuple(db.execute("""SELECT review_status,evidence_qualification,
                evidence_evaluation_id FROM operation_attachments WHERE id=?""",
                (uploaded.json['id'],)).fetchone())
            session_count = db.execute('SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0]
        self.assertNotEqual(first_cycle, second_cycle)
        self.assertEqual(after_item, before_item)
        self.assertEqual(after_attachment, before_attachment)
        self.assertEqual(session_count, 0)

    def test_watermark_retake_rejects_old_out_of_range_and_duplicate_originals(self):
        required_at, _ = self.set_retake_required()
        scenarios = (
            ('old', (datetime.now() - timedelta(minutes=5)).strftime('%Y.%m.%d %H:%M'),
             28.071303, 115.539684, 'ANTIOLD12345', '早于本轮补拍要求'),
            ('wrong-site', datetime.now().strftime('%Y.%m.%d %H:%M'),
             30.0, 116.0, 'ANTIWRONG123', '超过500米'),
        )
        for index, (label, taken_at, lat, lng, code, reason_fragment) in enumerate(scenarios):
            with self.subTest(label=label):
                app_module._recognize_watermark = lambda _, text=(
                    f'时间:{taken_at}\n经纬度:{lat:.6f} N,{lng:.6f} E\n防伪{code}'
                ): {'text': text, 'confidence': 0.99, 'status': 'recognized'}
                response = self.upload(
                    jpeg_bytes(('pink', 'cyan')[index]), capture_source='watermark_album',
                    _idempotency_key=f'retake-watermark-{label}',
                )
                self.assertEqual(response.status_code, 200, response.json)
                self.assertFalse(response.json['accepted_for_review'])
                self.assertFalse(response.json['can_keep_as_supplement'])
                self.assertIn(reason_fragment, response.json['reason'])

        duplicate_image = jpeg_bytes('lime')
        duplicate_hash = hashlib.sha256(duplicate_image).hexdigest()
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (filename,stored_path,source_type,source_id,site_id,uploader_id,sha256_hash,
                 review_status,evidence_qualification)
                VALUES ('prior.jpg','/uploads/prior.jpg','inspection',100,1,2,?,
                        'rejected','qualified')""", (duplicate_hash,))
        self.set_valid_watermark()
        duplicate = self.upload(
            duplicate_image, capture_source='watermark_album',
            _idempotency_key='retake-watermark-duplicate',
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.json)
        self.assertFalse(duplicate.json['accepted_for_review'])
        self.assertIn('重复', duplicate.json['reason'])
        with app_module.get_db() as db:
            item = db.execute("""SELECT review_status,evidence_status,rework_required_at,
                photo_urls FROM insp_plan_items WHERE id=100""").fetchone()
            formal_count = db.execute(
                "SELECT COUNT(*) FROM operation_attachments WHERE stored_path LIKE '/uploads/site_photos/%'"
            ).fetchone()[0]
        self.assertEqual((item['review_status'], item['evidence_status'], item['rework_required_at']),
                         (3, 'supplement_required', required_at))
        self.assertEqual(json.loads(item['photo_urls']), [])
        self.assertEqual(formal_count, 0)

    def test_invalid_retake_sessions_leave_attachment_and_session_unchanged(self):
        required_at, _ = self.set_retake_required()
        scenarios = ('missing', 'expired', 'used', 'wrong_item', 'wrong_cycle')
        for index, scenario in enumerate(scenarios):
            with self.subTest(scenario=scenario):
                session = self.create_capture_session(capture_source='camera')
                self.assertEqual(session.status_code, 200, session.json)
                token = session.json['capture_session']
                with app_module.get_db() as db:
                    row = db.execute(
                        'SELECT id FROM photo_capture_sessions ORDER BY id DESC LIMIT 1'
                    ).fetchone()
                    if scenario == 'expired':
                        db.execute("UPDATE photo_capture_sessions SET expires_at=datetime('now','-1 minute') WHERE id=?",
                                   (row['id'],))
                    elif scenario == 'used':
                        db.execute("UPDATE photo_capture_sessions SET used_at=datetime('now') WHERE id=?",
                                   (row['id'],))
                    elif scenario == 'wrong_cycle':
                        db.execute("UPDATE photo_capture_sessions SET rework_required_at='2000-01-01 00:00:00' WHERE id=?",
                                   (row['id'],))
                    elif scenario == 'wrong_item':
                        db.execute('UPDATE photo_capture_sessions SET item_id=999 WHERE id=?',
                                   (row['id'],))
                response = self.upload(
                    jpeg_bytes(('red', 'green', 'blue', 'purple', 'orange')[index]),
                    capture_source='camera',
                    capture_session=None if scenario == 'missing' else token,
                    rework_required_at=required_at,
                    taken_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    gps_lat=28.071303, gps_lng=115.539684,
                    _idempotency_key=f'invalid-retake-{scenario}',
                )
                self.assertEqual(response.status_code, 200, response.json)
                self.assertFalse(response.json['accepted_for_review'])
                self.assertEqual(response.json['code'], 'RETAKE_CAPTURE_SESSION_REQUIRED')
                with app_module.get_db() as db:
                    self.assertEqual(
                        db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
                    item = db.execute("""SELECT result,review_status,evidence_status,
                        rework_required_at,check_out_time FROM insp_plan_items WHERE id=100""").fetchone()
                    unchanged = db.execute(
                        'SELECT used_at,attachment_id FROM photo_capture_sessions WHERE id=?',
                        (row['id'],)
                    ).fetchone()
                if scenario == 'used':
                    self.assertTrue(unchanged['used_at'])
                else:
                    self.assertIsNone(unchanged['used_at'])
                self.assertIsNone(unchanged['attachment_id'])
                self.assertEqual(
                    (item['result'], item['review_status'], item['evidence_status'], item['rework_required_at']),
                    ('normal', 3, 'supplement_required', required_at),
                )
                photo_dir = os.path.join(self.upload_dir, 'site_photos')
                self.assertFalse(os.path.isdir(photo_dir) and os.listdir(photo_dir))

    def test_ocr_time_rework_cycle_change_rejects_stale_upload_without_side_effects(self):
        required_at, _ = self.set_retake_required()
        next_required_at = (datetime.now() + timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')
        self.assertNotEqual(required_at, next_required_at)
        now = datetime.now().strftime('%Y.%m.%d %H:%M')

        def recognize(_):
            with app_module.get_db() as db:
                db.execute('UPDATE insp_plan_items SET rework_required_at=? WHERE id=100',
                           (next_required_at,))
            return {
                'text': f'时间:{now}\n经纬度:28.071303 N,115.539684 E\n防伪RACECYCLE123',
                'confidence': 0.99, 'status': 'recognized',
            }

        app_module._recognize_watermark = recognize
        response = self.upload(
            jpeg_bytes('plum'), capture_source='watermark_album',
            _idempotency_key='race-cycle',
        )
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'INSPECTION_UPLOAD_CONTEXT_CHANGED')
        with app_module.get_db() as db:
            item = db.execute("""SELECT review_status,evidence_status,rework_required_at,
                photo_urls,actual_photos FROM insp_plan_items WHERE id=100""").fetchone()
        self.assertEqual((item['review_status'], item['evidence_status'], item['rework_required_at']),
                         (3, 'supplement_required', next_required_at))
        self.assertEqual((json.loads(item['photo_urls']), item['actual_photos']), ([], 0))
        self.assert_no_upload_artifacts()

    def test_ocr_time_approval_freezes_upload_without_side_effects(self):
        now = datetime.now().strftime('%Y.%m.%d %H:%M')

        def recognize(_):
            with app_module.get_db() as db:
                db.execute("""UPDATE insp_plan_items
                    SET result='normal',review_status=2,evidence_status='effective' WHERE id=100""")
            return {
                'text': f'时间:{now}\n经纬度:28.071303 N,115.539684 E\n防伪RACEAPPROVED123',
                'confidence': 0.99, 'status': 'recognized',
            }

        app_module._recognize_watermark = recognize
        response = self.upload(
            jpeg_bytes('indigo'), capture_source='watermark_album',
            _idempotency_key='race-approved',
        )
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'INSPECTION_ITEM_APPROVED')
        with app_module.get_db() as db:
            item = db.execute("""SELECT result,review_status,evidence_status,
                photo_urls,actual_photos FROM insp_plan_items WHERE id=100""").fetchone()
        self.assertEqual((item['result'], item['review_status'], item['evidence_status']),
                         ('normal', 2, 'effective'))
        self.assertEqual((json.loads(item['photo_urls']), item['actual_photos']), ([], 0))
        self.assert_no_upload_artifacts()

    def test_already_approved_item_rejects_session_and_upload_without_side_effects(self):
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET result='normal',review_status=2,evidence_status='effective' WHERE id=100""")
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 409, session.json)
        self.assertEqual(session.json['code'], 'INSPECTION_ITEM_APPROVED')
        upload = self.upload(
            jpeg_bytes('salmon'), capture_source='watermark_album',
            _idempotency_key='already-approved',
        )
        self.assertEqual(upload.status_code, 409, upload.json)
        self.assertEqual(upload.json['code'], 'INSPECTION_ITEM_APPROVED')
        with app_module.get_db() as db:
            item = db.execute("""SELECT result,review_status,evidence_status,
                photo_urls,actual_photos FROM insp_plan_items WHERE id=100""").fetchone()
        self.assertEqual((item['result'], item['review_status'], item['evidence_status']),
                         ('normal', 2, 'effective'))
        self.assertEqual((json.loads(item['photo_urls']), item['actual_photos']), ([], 0))
        self.assert_no_upload_artifacts()

    def test_ocr_time_cancellation_rejects_upload_without_side_effects(self):
        now = datetime.now().strftime('%Y.%m.%d %H:%M')

        def recognize(_):
            with app_module.get_db() as db:
                db.execute("UPDATE insp_plan_items SET execution_status='cancelled' WHERE id=100")
            return {
                'text': f'时间:{now}\n经纬度:28.071303 N,115.539684 E\n防伪RACECANCELLED123',
                'confidence': 0.99, 'status': 'recognized',
            }

        app_module._recognize_watermark = recognize
        response = self.upload(
            jpeg_bytes('tan'), capture_source='watermark_album',
            _idempotency_key='race-cancelled',
        )
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'INSPECTION_ITEM_CANCELLED')
        with app_module.get_db() as db:
            item = db.execute("""SELECT execution_status,photo_urls,actual_photos
                FROM insp_plan_items WHERE id=100""").fetchone()
        self.assertEqual((item['execution_status'], json.loads(item['photo_urls']), item['actual_photos']),
                         ('cancelled', [], 0))
        self.assert_no_upload_artifacts()

    def test_ocr_time_assignee_change_rejects_upload_without_side_effects(self):
        now = datetime.now().strftime('%Y.%m.%d %H:%M')

        def recognize(_):
            with app_module.get_db() as db:
                db.execute('UPDATE insp_plans SET assignee_id=3 WHERE id=10')
            return {
                'text': f'时间:{now}\n经纬度:28.071303 N,115.539684 E\n防伪RACEASSIGNEE123',
                'confidence': 0.99, 'status': 'recognized',
            }

        app_module._recognize_watermark = recognize
        response = self.upload(
            jpeg_bytes('orchid'), capture_source='watermark_album',
            _idempotency_key='race-assignee',
        )
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'INSPECTION_ITEM_ACCESS_CHANGED')
        with app_module.get_db() as db:
            plan = db.execute('SELECT assignee_id FROM insp_plans WHERE id=10').fetchone()
            item = db.execute(
                'SELECT photo_urls,actual_photos FROM insp_plan_items WHERE id=100').fetchone()
        self.assertEqual(plan['assignee_id'], 3)
        self.assertEqual((json.loads(item['photo_urls']), item['actual_photos']), ([], 0))
        self.assert_no_upload_artifacts()

    def test_old_idempotency_key_does_not_replay_previous_rework_cycle(self):
        self.set_retake_required()
        self.set_valid_watermark()
        first = self.upload(
            jpeg_bytes('crimson'), capture_source='watermark_album',
            _idempotency_key='reused-across-cycles',
        )
        self.assertEqual(first.status_code, 200, first.json)
        self.assertTrue(first.json['accepted_for_review'])

        next_required_at = (datetime.now().replace(second=0, microsecond=0)
                            - timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET review_status='rejected' WHERE id=?",
                       (first.json['id'],))
            db.execute("""UPDATE insp_plan_items SET review_status=3,
                evidence_status='supplement_required',rework_required_at=? WHERE id=100""",
                (next_required_at,))
        now = datetime.now().strftime('%Y.%m.%d %H:%M')
        app_module._recognize_watermark = lambda _: {
            'text': f'时间:{now}\n经纬度:28.071303 N,115.539684 E\n防伪NEXTCYCLE1234',
            'confidence': 0.99, 'status': 'recognized',
        }
        second = self.upload(
            jpeg_bytes('azure'), capture_source='watermark_album',
            _idempotency_key='reused-across-cycles',
        )
        self.assertEqual(second.status_code, 200, second.json)
        self.assertTrue(second.json['accepted_for_review'])
        self.assertNotEqual(second.json['id'], first.json['id'])
        with app_module.get_db() as db:
            rows = db.execute("""SELECT id,review_status FROM operation_attachments
                ORDER BY id""").fetchall()
            cache = db.execute("""SELECT endpoint FROM mobile_idempotency
                WHERE idempotency_key=?""", ('2:reused-across-cycles',)).fetchone()
        self.assertEqual([(row['id'], row['review_status']) for row in rows],
                         [(first.json['id'], 'rejected'), (second.json['id'], 'pending')])
        self.assertTrue(cache['endpoint'].endswith(next_required_at))

    def test_inspection_upload_faults_leave_no_database_rows_cache_or_orphan_file(self):
        failure_points = (
            ('evaluation', '_record_attachment_evaluation'),
            ('association', '_persist_inspection_attachment_link'),
            ('flagging', '_flag_attachment'),
        )
        for index, (label, target) in enumerate(failure_points):
            with self.subTest(failure=label):
                session = self.create_capture_session()
                self.assertEqual(session.status_code, 200, session.json)
                with mock.patch.object(app_module, target,
                                       side_effect=RuntimeError(f'injected {label} failure')):
                    response = self.upload(
                        jpeg_bytes(('maroon', 'olive', 'aqua')[index]),
                        capture_source='camera',
                        capture_session=session.json['capture_session'],
                        taken_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        gps_lat=28.071303, gps_lng=115.539684,
                        _idempotency_key=f'inspection-{label}-failure',
                    )
                self.assertEqual(response.status_code, 500, response.json)
                with app_module.get_db() as db:
                    self.assertEqual(db.execute(
                        'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
                    self.assertEqual(db.execute(
                        'SELECT COUNT(*) FROM attachment_evidence_evaluations').fetchone()[0], 0)
                    self.assertEqual(db.execute(
                        'SELECT COUNT(*) FROM mobile_idempotency').fetchone()[0], 0)
                    capture = db.execute("""SELECT used_at,attachment_id
                        FROM photo_capture_sessions ORDER BY id DESC LIMIT 1""").fetchone()
                self.assertIsNone(capture['used_at'])
                self.assertIsNone(capture['attachment_id'])
                photo_dir = os.path.join(self.upload_dir, 'site_photos')
                self.assertFalse(os.path.isdir(photo_dir) and os.listdir(photo_dir))

    def test_absolute_client_url_normalizes_to_stored_path(self):
        self.assertEqual(app_module._attachment_storage_path(
            'http://127.0.0.1:5021/uploads/site_photos/a.jpg?display=1'),
            '/uploads/site_photos/a.jpg')


if __name__ == '__main__':
    unittest.main()
