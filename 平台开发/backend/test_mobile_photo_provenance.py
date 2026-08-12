import base64
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


def jpeg_with_capture_time(color, taken_at='2026:07:24 14:12:26'):
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[36867] = taken_at
    Image.new('RGB', (32, 24), color).save(buffer, format='JPEG', exif=exif)
    return buffer.getvalue()


class MobilePhotoProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.upload_dir = os.path.join(self.temp_dir, 'uploads')
        self.original_get_db = app_module.get_db
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.original_tokens = dict(app_module._tokens)
        self.original_recognize_watermark = app_module._recognize_watermark
        app_module._recognize_watermark = lambda _: {
            'text': '', 'confidence': None, 'status': 'unreadable'
        }

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
            'id': 2, 'role': 'operator', 'real_name': '现场运维', 'username': 'operator'
        }
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE sites (id INTEGER PRIMARY KEY, gps_lat REAL, gps_lng REAL);
                INSERT INTO sites VALUES (1, 28.071303, 115.539684);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                INSERT INTO user_sites VALUES (2, 1);
                CREATE TABLE photo_requirements (
                    id INTEGER PRIMARY KEY, item_name TEXT, review_required INTEGER,
                    watermark_keyword TEXT, category TEXT
                );
                CREATE TABLE mobile_idempotency (
                    idempotency_key TEXT PRIMARY KEY, endpoint TEXT,
                    response_json TEXT, created_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT, stored_path TEXT, file_type TEXT, mime_type TEXT,
                    file_size INTEGER, description TEXT, source_type TEXT, source_id INTEGER,
                    plan_id INTEGER, item_id INTEGER, item_name TEXT DEFAULT '',
                    site_id INTEGER, uploader_id INTEGER, uploader_name TEXT,
                    gps_lat REAL, gps_lng REAL, taken_at TEXT, created_at TEXT,
                    category TEXT, capture_source TEXT, sha256_hash TEXT,
                    duplicate_of_id INTEGER, perceptual_hash TEXT DEFAULT '',
                    watermark_code TEXT DEFAULT '', watermark_text TEXT DEFAULT '',
                    recognized_category TEXT DEFAULT '', match_status TEXT DEFAULT 'manual',
                    match_confidence REAL, review_required INTEGER DEFAULT 0,
                    requirement_id INTEGER, review_status TEXT DEFAULT 'pending',
                    extra_json TEXT, is_deleted INTEGER DEFAULT 0,
                    is_flagged INTEGER DEFAULT 0, flag_reason TEXT DEFAULT '', flag_rule TEXT DEFAULT ''
                );
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module.UPLOAD_DIR = self.original_upload_dir
        app_module._recognize_watermark = self.original_recognize_watermark
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        shutil.rmtree(self.temp_dir)

    @staticmethod
    def headers():
        return {'Authorization': 'Bearer operator-token'}

    def upload(self, image_bytes, **metadata):
        image = 'data:image/jpeg;base64,' + base64.b64encode(image_bytes).decode('ascii')
        payload = {'site_id': 1, 'image': image, **metadata}
        return self.client.post('/api/mobile/upload-site-photo', headers=self.headers(), json=payload)

    def test_watermark_album_uses_original_exif_capture_time(self):
        response = self.upload(jpeg_with_capture_time('blue'), capture_source='watermark_album')

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['taken_at'], '2026-07-24 14:12:26')
        self.assertEqual(response.json['capture_source'], 'watermark_album')
        with app_module.get_db() as db:
            row = db.execute('SELECT * FROM operation_attachments').fetchone()
        self.assertEqual(row['taken_at'], '2026-07-24 14:12:26')
        self.assertEqual(len(row['sha256_hash']), 64)
        self.assertIn('"metadata_source": "exif"', row['extra_json'])

    def test_unreadable_watermark_is_neutral_instead_of_claiming_it_is_missing(self):
        buffer = io.BytesIO()
        Image.new('RGB', (64, 48), 'white').save(buffer, format='JPEG')
        response = self.upload(buffer.getvalue(), capture_source='watermark_album')

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['watermark_status'], 'unreadable')
        self.assertFalse(response.json['review_required'])
        self.assertNotIn('未能识别水印内容', response.json['risk_reasons'])

    def test_inspection_upload_is_bound_to_its_item_at_creation(self):
        with app_module.get_db() as db:
            db.executescript('''
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, status TEXT,
                    plan_schedule_id INTEGER, start_date TEXT, plan_date TEXT
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    item_name TEXT, category TEXT, result TEXT
                );
                INSERT INTO plan_schedules VALUES (5,'approved');
                INSERT INTO insp_plans VALUES (10,2,'active',5,NULL,NULL);
                INSERT INTO insp_plan_items VALUES (100,10,1,'浊度仪表读数','设备检查',NULL);
            ''')
        response = self.upload(
            jpeg_with_capture_time('navy'), capture_source='watermark_album',
            plan_id=10, item_id=100, item_name='客户端伪造名称',
        )

        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            row = db.execute("""SELECT source_type,source_id,plan_id,item_id,item_name,
                       description,category,recognized_category,extra_json FROM operation_attachments""").fetchone()
        self.assertEqual((row['source_type'], row['source_id']), ('inspection', 100))
        self.assertEqual((row['plan_id'], row['item_id'], row['item_name']), (10, 100, '浊度仪表读数'))
        self.assertEqual(row['description'], '浊度仪表读数')
        self.assertEqual(row['category'], '设备检查')
        self.assertEqual(row['recognized_category'], '浊度仪表读数')
        self.assertIn('"item_id": 100', row['extra_json'])

    def test_exact_duplicate_is_accepted_and_marked_for_review(self):
        image = jpeg_with_capture_time('green')
        first = self.upload(image, capture_source='watermark_album')
        second = self.upload(image, capture_source='watermark_album')

        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertFalse(first.json['duplicate'])
        self.assertTrue(second.json['duplicate'])
        self.assertEqual(second.json['duplicate_of_id'], first.json['id'])
        with app_module.get_db() as db:
            row = db.execute(
                'SELECT is_flagged, flag_reason FROM operation_attachments WHERE id=?',
                (second.json['id'],),
            ).fetchone()
        self.assertEqual(row['is_flagged'], 1)
        self.assertIn('完全相同', row['flag_reason'])

    def test_replay_with_same_idempotency_key_returns_original_record(self):
        image = jpeg_with_capture_time('cyan')
        first = self.upload(image, capture_source='watermark_album', _idempotency_key='photo-replay-1')
        second = self.upload(image, capture_source='watermark_album', _idempotency_key='photo-replay-1')

        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(second.json['id'], first.json['id'])
        self.assertEqual(second.json['url'], first.json['url'])
        with app_module.get_db() as db:
            count = db.execute('SELECT COUNT(*) AS c FROM operation_attachments').fetchone()['c']
        self.assertEqual(count, 1)

    def test_camera_metadata_is_kept_as_field_capture_evidence(self):
        response = self.upload(
            jpeg_with_capture_time('red'), capture_source='camera',
            taken_at='2026-08-03 15:30:00', gps_lat=28.071303, gps_lng=115.539684,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['taken_at'], '2026-08-03 15:30:00')
        self.assertAlmostEqual(response.json['gps_lat'], 28.071303)

    def test_flag_evaluation_failure_does_not_rollback_uploaded_attachment(self):
        original = app_module._evaluate_attachment_flag

        def fail_flag_evaluation(*_args, **_kwargs):
            raise RuntimeError('TEST_UI_MEDIA_FIX_flag_failure')

        app_module._evaluate_attachment_flag = fail_flag_evaluation
        try:
            response = self.upload(
                jpeg_with_capture_time('yellow'),
                capture_source='camera',
                taken_at='2026-08-03 15:31:00',
                gps_lat=28.071303,
                gps_lng=115.539684,
            )
        finally:
            app_module._evaluate_attachment_flag = original

        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            row = db.execute(
                'SELECT id, stored_path FROM operation_attachments WHERE id=?',
                (response.json['id'],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['stored_path'], response.json['url'])

    def test_absolute_client_url_normalizes_to_stored_upload_path(self):
        self.assertEqual(
            app_module._attachment_storage_path('http://192.168.2.113:5000/uploads/site_photos/a.jpg'),
            '/uploads/site_photos/a.jpg',
        )

    def test_watermark_parser_extracts_time_position_and_antifake_code(self):
        parsed = app_module._parse_watermark_fields(
            '时间: 2026.07.24 14:12\n经纬度: 28.071303 N,115.539684 E\n防伪WRWYCRY1K14X34'
        )
        self.assertEqual(parsed['taken_at'], '2026-07-24 14:12:00')
        self.assertAlmostEqual(parsed['gps_lat'], 28.071303)
        self.assertAlmostEqual(parsed['gps_lng'], 115.539684)
        self.assertEqual(parsed['code'], 'WRWYCRY1K14X34')

    def test_difference_hash_treats_jpeg_recompression_as_near_duplicate(self):
        original = jpeg_with_capture_time('purple')
        image = Image.open(io.BytesIO(original))
        recompressed = io.BytesIO()
        image.save(recompressed, format='JPEG', quality=55)
        left = app_module._image_difference_hash(original)
        right = app_module._image_difference_hash(recompressed.getvalue())
        self.assertLessEqual(app_module._hash_distance(left, right), 6)

    def test_recognized_watermark_fields_are_saved_with_the_attachment(self):
        app_module._recognize_watermark = lambda _: {
            'text': '时间:2026.07.24 14:12\n经纬度:28.071303 N,115.539684 E\n防伪WRWYCRY1K14X34',
            'confidence': 0.99,
            'status': 'recognized',
        }

        response = self.upload(jpeg_with_capture_time('orange'), capture_source='watermark_album')

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['watermark_code'], 'WRWYCRY1K14X34')
        self.assertEqual(response.json['watermark_status'], 'recognized')
        with app_module.get_db() as db:
            row = db.execute('SELECT watermark_code, watermark_text FROM operation_attachments').fetchone()
        self.assertEqual(row['watermark_code'], 'WRWYCRY1K14X34')
        self.assertIn('2026.07.24', row['watermark_text'])


if __name__ == '__main__':
    unittest.main()
