import base64
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
                    completed_at TEXT, check_time TEXT
                );
                INSERT INTO insp_plan_items VALUES
                    (100,10,1,'浊度仪表读数','设备检查',NULL,NULL,NULL,NULL,NULL);
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
                    issued_at TEXT, expires_at TEXT, used_at TEXT, attachment_id INTEGER
                );
            ''')
            db.execute('INSERT INTO inspection_checkins VALUES (1,1,2,?)',
                       ((now - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S'),))
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

    def test_absolute_client_url_normalizes_to_stored_path(self):
        self.assertEqual(app_module._attachment_storage_path(
            'http://127.0.0.1:5021/uploads/site_photos/a.jpg?display=1'),
            '/uploads/site_photos/a.jpg')


if __name__ == '__main__':
    unittest.main()
