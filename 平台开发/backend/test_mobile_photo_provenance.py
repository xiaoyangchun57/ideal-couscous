import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit, parse_qs
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


def jpeg_bytes(color='white'):
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), color).save(buffer, format='JPEG')
    return buffer.getvalue()


def portrait_jpeg_bytes(color='white'):
    buffer = io.BytesIO()
    Image.new('RGB', (720, 960), color).save(buffer, format='JPEG')
    return buffer.getvalue()


def patterned_jpeg_bytes():
    buffer = io.BytesIO()
    image = Image.new('RGB', (64, 48), 'black')
    for x in range(64):
        for y in range(48):
            value = (x * 37 + y * 73 + x * y * 11) % 256
            image.putpixel((x, y), (value, (value * 3) % 256, (255 - value)))
    image.save(buffer, format='JPEG')
    return buffer.getvalue()


def image_bytes(image_format, color):
    buffer = io.BytesIO()
    image = Image.new('RGB', (128, 96), color)
    for x in range(128):
        for y in range(96):
            value = (x * 19 + y * 31 + x * y * 3) % 256
            image.putpixel((x, y), (value, (value * 5) % 256, (255 - value)))
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def exif_jpeg_bytes():
    buffer = io.BytesIO()
    taken_at = datetime.now().strftime('%Y:%m:%d %H:%M:%S')
    exif = Image.Exif()
    exif[274] = 6
    exif[36867] = taken_at
    Image.new('RGB', (80, 60), 'purple').save(
        buffer, format='JPEG', quality=95, exif=exif)
    return buffer.getvalue(), taken_at


def watermark_jpeg_bytes():
    image = Image.new('RGB', (1600, 900), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('DejaVuSans.ttf', 48)
    y = 590
    for line in ('2026-10-08', '14:35', '28.071303, 115.539684', 'WM20261008A123'):
        draw.text((80, y), line, fill='black', font=font)
        y += 68
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=95)
    return buffer.getvalue()


def exif_oriented_watermark_jpeg_bytes():
    visual = Image.new('RGB', (900, 1600), 'white')
    draw = ImageDraw.Draw(visual)
    font = ImageFont.truetype('DejaVuSans.ttf', 46)
    y = 1260
    for line in ('2026-10-09', '16:20', 'LAT 28.672656',
                 'LNG 115.869296', 'ORIENT20261009A'):
        draw.text((70, y), line, fill='black', font=font)
        y += 58
    stored = visual.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    stored.save(buffer, format='JPEG', quality=95, exif=exif)
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
            except Exception:
                db.rollback()
                raise
            else:
                db.commit()
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module.UPLOAD_DIR = self.upload_dir
        app_module._tokens.clear()
        app_module._tokens['operator-token'] = {
            'id': 2, 'role': 'operator', 'real_name': '现场运维', 'username': 'operator',
        }
        app_module._tokens['reviewer-token'] = {
            'id': 3, 'role': 'reviewer', 'real_name': '审核员', 'username': 'reviewer',
        }
        app_module._tokens['admin-token'] = {
            'id': 5, 'role': 'admin', 'roles': ['admin'],
            'real_name': '管理员', 'username': 'admin',
        }
        app_module._tokens['outsider-token'] = {
            'id': 4, 'role': 'operator', 'real_name': '其他站运维', 'username': 'outsider',
        }
        now = datetime.now()
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, gps_lat REAL, gps_lng REAL);
                INSERT INTO sites VALUES (1,'测试站',28.071303,115.539684);
                INSERT INTO sites VALUES (2,'其他站',29.0,116.0);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                INSERT INTO user_sites VALUES (2,1);
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT
                );
                INSERT INTO users VALUES (2,'现场运维','operator','active');
                INSERT INTO users VALUES (3,'审核员','reviewer','active');
                INSERT INTO users VALUES (4,'其他站运维','operator','active');
                INSERT INTO users VALUES (5,'管理员','admin','active');
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                INSERT INTO user_roles VALUES (3,'reviewer');
                INSERT INTO user_sites VALUES (3,1);
                INSERT INTO user_sites VALUES (4,2);
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                INSERT INTO plan_schedules VALUES (5,'approved');
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, status TEXT,
                    plan_schedule_id INTEGER, start_date TEXT, plan_date TEXT,
                    completion_rate REAL DEFAULT 0
                );
                INSERT INTO insp_plans VALUES (10,2,'active',5,NULL,NULL,0);
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
                    id INTEGER PRIMARY KEY, site_id INTEGER, user_id INTEGER,
                    check_time TEXT, plan_id INTEGER
                );
                CREATE TABLE photo_requirements (
                    id INTEGER PRIMARY KEY, item_name TEXT, review_required INTEGER,
                    watermark_keyword TEXT, category TEXT
                );
                CREATE TABLE mobile_idempotency (
                    idempotency_key TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT, created_at TEXT
                );
                CREATE TABLE timeline_events (
                    source_type TEXT, source_id INTEGER, event_type TEXT,
                    operator TEXT, remark TEXT
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
                    rework_required_at TEXT, capture_source TEXT DEFAULT '',
                    nonce TEXT DEFAULT '', anti_counterfeit_code TEXT DEFAULT ''
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    source_type TEXT, source_id INTEGER, title TEXT, content TEXT,
                    is_read INTEGER DEFAULT 0
                );
                CREATE TABLE work_orders (id INTEGER PRIMARY KEY, order_no TEXT);
            ''')
            db.execute('INSERT INTO inspection_checkins VALUES (1,1,2,?,10)',
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

    def upload(self, image, *, _trusted_camera_defaults=True, **metadata):
        if _trusted_camera_defaults and metadata.get('capture_source') == 'camera':
            with Image.open(io.BytesIO(image)) as source_image:
                if source_image.size != (720, 960):
                    normalized = io.BytesIO()
                    source_image.convert('RGB').resize((720, 960)).save(
                        normalized, format='JPEG', quality=95)
                    image = normalized.getvalue()
            metadata = {
                'capture_client': 'trusted_camera_v1',
                'capture_frame_orientation': 'portrait',
                'capture_frame_ratio': '3:4',
                'capture_frame_mode': 'center_cover',
                **metadata,
            }
        payload = {
            'site_id': 1,
            'image': 'data:image/jpeg;base64,' + base64.b64encode(image).decode('ascii'),
            'plan_id': 10, 'item_id': 100, 'item_name': '客户端伪造名称',
            **metadata,
        }
        return self.client.post('/api/mobile/upload-site-photo', headers=self.headers(), json=payload)

    def set_valid_watermark(self, code='WRWYCRY1K14X34'):
        now = datetime.now().strftime('%Y.%m.%d %H:%M')
        app_module._recognize_watermark = lambda _: {
            'text': f'时间:{now}\n经纬度:28.071303 N,115.539684 E\n防伪{code}',
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

    def photo_file_counts(self):
        display_dir = os.path.join(self.upload_dir, 'site_photos')
        private_dir = app_module._private_site_photo_root()
        return (
            len(os.listdir(display_dir)) if os.path.isdir(display_dir) else 0,
            len(os.listdir(private_dir)) if os.path.isdir(private_dir) else 0,
        )

    def assert_camera_frame_contract_rejected(self, **metadata):
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        token = session.json['capture_session']
        response = self.upload(
            patterned_jpeg_bytes(),
            _trusted_camera_defaults=False,
            capture_source='camera',
            capture_session=token,
            gps_lat=28.071303,
            gps_lng=115.539684,
            _idempotency_key='invalid-camera-frame',
            **metadata,
        )
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'CAPTURE_FRAME_INVALID')
        self.assertNotIn('accepted_for_review', response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM attachment_evidence_evaluations').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM mobile_idempotency').fetchone()[0], 0)
            capture = db.execute(
                'SELECT used_at,attachment_id FROM photo_capture_sessions WHERE token_hash=?',
                (app_module._hash_token(token),)).fetchone()
            self.assertIsNotNone(capture)
            self.assertIsNone(capture['used_at'])
            self.assertIsNone(capture['attachment_id'])
        self.assertEqual(self.photo_file_counts(), (0, 0))
        return response

    def create_pending_site_photo(self, *, filename='pending.jpg', uploader_id=2,
                                  source_type='site_photo', source_id=0, stored_path=None,
                                  material_role=None, raw_extra_json=None):
        photo_dir = os.path.join(self.upload_dir, 'site_photos')
        os.makedirs(photo_dir, exist_ok=True)
        path = stored_path or f'/uploads/site_photos/{filename}'
        file_path = os.path.join(photo_dir, filename)
        with open(file_path, 'wb') as photo_file:
            photo_file.write(b'pending-photo')
        with app_module.get_db() as db:
            cursor = db.execute("""INSERT INTO operation_attachments
                (filename,stored_path,source_type,source_id,site_id,uploader_id,is_deleted,extra_json)
                VALUES (?,?,?,?,?,?,0,?)""",
                (filename, path, source_type, source_id, 1, uploader_id,
                 raw_extra_json if raw_extra_json is not None else
                 (json.dumps({'material_role': material_role}) if material_role else '{}')))
            attachment_id = cursor.lastrowid
        return attachment_id, path, file_path

    def delete_pending_site_photo(self, path):
        return self.client.post('/api/mobile/site-photos/delete', headers=self.headers(),
                                json={'url': path})



    def test_watermark_parser_accepts_single_ocr_separator_between_date_and_time(self):
        fields = app_module._parse_watermark_fields(
            '2026-09-30L\n16:21\n经纬度：28.672840°N,115.869488°E\n防伪PKUH2T136R4MKX')
        self.assertEqual(fields, {
            'taken_at': '2026-09-30 16:21:00',
            'gps_lat': 28.67284,
            'gps_lng': 115.869488,
            'code': 'PKUH2T136R4MKX',
        })

    def test_watermark_parser_pairs_layout_independent_fields_in_one_block(self):
        payload = {
            'text': '', 'image_size': [1200, 1600],
            'lines': [
                {'text': '09:26', 'box': [72, 1260, 245, 1320], 'confidence': 0.99},
                {'text': '2026-10-09 星期五 24°C', 'box': [275, 1258, 760, 1322],
                 'confidence': 0.98},
                {'text': '地址：示例路 88 号', 'box': [72, 1330, 610, 1380], 'confidence': 0.98},
                {'text': '经纬度：28.071303°N，115.539684°E',
                 'box': [72, 1390, 720, 1440], 'confidence': 0.99},
                {'text': '备注：站点 2026 编号 09', 'box': [72, 1450, 620, 1500],
                 'confidence': 0.97},
                {'text': '防伪码：LAYOUT20261009A', 'box': [72, 1510, 620, 1560],
                 'confidence': 0.99},
            ],
        }
        self.assertEqual(app_module._parse_watermark_fields(payload), {
            'taken_at': '2026-10-09 09:26:00',
            'gps_lat': 28.071303,
            'gps_lng': 115.539684,
            'code': 'LAYOUT20261009A',
        })

    def test_watermark_parser_supports_common_date_and_time_forms(self):
        cases = (
            ('2026-10-09\n09:26:31', '2026-10-09 09:26:31'),
            ('拍摄日期 2026/10/09\n上午9:26', '2026-10-09 09:26:00'),
            ('时间 下午 3时07分08秒\n2026.10.09', '2026-10-09 15:07:08'),
            ('日期：20261009\n9时6分', '2026-10-09 09:06:00'),
            ('20261009\n09:26\n坐标 28.071303, 115.539684\n防伪 UNIQUEBLOCK123',
             '2026-10-09 09:26:00'),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(app_module._parse_watermark_fields(value)['taken_at'], expected)
        self.assertIsNone(app_module._parse_watermark_fields('编号 20261009\n09:26')['taken_at'])

    def test_watermark_parser_preserves_coordinate_semantics_for_common_forms(self):
        cases = (
            ('28.071303°N，115.539684°E', (28.071303, 115.539684)),
            ('E115.539684 N28.071303', (28.071303, 115.539684)),
            ('经度：115.539684 纬度：28.071303', (28.071303, 115.539684)),
            ('115.539684, 28.071303', (28.071303, 115.539684)),
            ('S28.071303；W115.539684', (-28.071303, -115.539684)),
            ('LAT: 28.071303\nLNG: 115.539684', (28.071303, 115.539684)),
            ('LON/LAT: 115.539684, 28.071303', (28.071303, 115.539684)),
            ('北纬 28.071303\n东经 115.539684', (28.071303, 115.539684)),
        )
        for coordinate_text, expected in cases:
            with self.subTest(coordinate_text=coordinate_text):
                fields = app_module._parse_watermark_fields(
                    f'2026-10-09 09:26\n坐标 {coordinate_text}\n防伪 COORDS123456')
                self.assertEqual((fields['gps_lat'], fields['gps_lng']), expected)

    def test_watermark_parser_rejects_invalid_or_ambiguous_candidates(self):
        invalid_values = (
            '2026-02-30 09:26\n坐标 28.071303,115.539684\n防伪 INVALIDDATE1',
            '2026-10-09 25:61\n坐标 28.071303,115.539684\n防伪 INVALIDTIME1',
            '2026-10-09 09:26\n坐标 128.071303,215.539684\n防伪 INVALIDGPS1',
            '2026-10-09',
            '09:26',
            '地址：示例路 88 号',
        )
        for value in invalid_values:
            with self.subTest(value=value):
                fields = app_module._parse_watermark_fields(value)
                self.assertFalse(fields['taken_at'] and fields['gps_lat'] is not None
                                 and fields['gps_lng'] is not None and fields['code'])

        ambiguous = {
            'image_size': [1200, 1600],
            'lines': [
                {'text': '2026-10-09 09:26', 'box': [50, 80, 380, 130]},
                {'text': '坐标 28.071303,115.539684', 'box': [50, 145, 500, 195]},
                {'text': '防伪 FIRSTBLOCK123', 'box': [50, 210, 420, 260]},
                {'text': '2026-10-09 10:26', 'box': [650, 1250, 980, 1300]},
                {'text': '坐标 28.071303,115.539684', 'box': [650, 1315, 1100, 1365]},
                {'text': '防伪 SECONDBLOCK123', 'box': [650, 1380, 1050, 1430]},
            ],
        }
        self.assertEqual(app_module._parse_watermark_fields(ambiguous), {
            'taken_at': None, 'gps_lat': None, 'gps_lng': None, 'code': '',
        })

        for extra_line in (
                {'text': '2026-10-08', 'box': [55, 1270, 300, 1320]},
                {'text': '10:26', 'box': [540, 1270, 700, 1320]},
                {'text': '坐标 29.071303,116.539684', 'box': [55, 1410, 620, 1460]}):
            with self.subTest(extra_line=extra_line['text']):
                conflicting = {
                    'image_size': [1200, 1600],
                    'lines': [
                        {'text': '2026-10-09 09:26', 'box': [55, 1330, 500, 1380]},
                        {'text': '坐标 28.071303,115.539684', 'box': [55, 1390, 620, 1440]},
                        {'text': '防伪 CONFLICTBLOCK123', 'box': [55, 1470, 560, 1520]},
                        extra_line,
                    ],
                }
                self.assertEqual(app_module._parse_watermark_fields(conflicting), {
                    'taken_at': None, 'gps_lat': None, 'gps_lng': None, 'code': '',
                })

    def test_semantically_equal_ocr_observations_choose_best_sources(self):
        repeated = {
            'image_size': [1200, 1600],
            'lines': [
                {'text': '2026-10-09', 'box': [55, 1260, 300, 1310],
                 'confidence': 0.96, 'region': 'bottom'},
                {'text': '2026-10-09', 'box': [58, 1262, 303, 1312],
                 'confidence': 0.99, 'region': 'full'},
                {'text': '09:26', 'box': [330, 1260, 480, 1310],
                 'confidence': 0.97, 'region': 'bottom'},
                {'text': '09:26', 'box': [333, 1262, 483, 1312],
                 'confidence': 0.95, 'region': 'full'},
                {'text': '经纬度：28.672656°N,115.869296E',
                 'box': [55, 1330, 680, 1380], 'confidence': 0.93, 'region': 'bottom'},
                {'text': '经纬度：28.672656°N,115.869296°E',
                 'box': [58, 1332, 683, 1382], 'confidence': 0.98, 'region': 'full'},
                {'text': '防伪 DUPLICATESOURCE123', 'box': [55, 1400, 580, 1450],
                 'confidence': 0.94, 'region': 'bottom'},
                {'text': '防伪 DUPLICATESOURCE123', 'box': [58, 1402, 583, 1452],
                 'confidence': 0.99, 'region': 'full'},
            ],
        }
        fields = app_module._parse_watermark_fields(repeated, include_metadata=True)
        self.assertEqual({key: fields[key] for key in ('taken_at', 'gps_lat', 'gps_lng', 'code')}, {
            'taken_at': '2026-10-09 09:26:00',
            'gps_lat': 28.672656,
            'gps_lng': 115.869296,
            'code': 'DUPLICATESOURCE123',
        })
        self.assertEqual(fields['confidence'], 0.97)
        self.assertEqual(fields['confidence_source_indexes'], [1, 2, 5, 7])

    def test_distinct_semantic_ocr_observations_remain_conflicts(self):
        base_lines = [
            {'text': '2026-10-09', 'box': [55, 1260, 300, 1310], 'confidence': 0.99},
            {'text': '09:26', 'box': [330, 1260, 480, 1310], 'confidence': 0.99},
            {'text': '经纬度：28.672656°N,115.869296°E',
             'box': [55, 1330, 680, 1380], 'confidence': 0.99},
            {'text': '防伪 SEMANTICVALUE123', 'box': [55, 1400, 580, 1450],
             'confidence': 0.99},
        ]
        conflicts = (
            {'text': '10:26', 'box': [500, 1260, 650, 1310], 'confidence': 0.99},
            {'text': '经纬度：29.672656°N,116.869296°E',
             'box': [58, 1332, 683, 1382], 'confidence': 0.99},
            {'text': '防伪 DIFFERENTVALUE123', 'box': [58, 1402, 583, 1452],
             'confidence': 0.99},
        )
        for conflicting_line in conflicts:
            with self.subTest(conflicting_line=conflicting_line['text']):
                payload = {'image_size': [1200, 1600],
                           'lines': [dict(line) for line in base_lines] + [conflicting_line]}
                self.assertEqual(app_module._parse_watermark_fields(payload), {
                    'taken_at': None, 'gps_lat': None, 'gps_lng': None, 'code': '',
                })

    def test_watermark_rejection_diagnostic_redacts_values_but_preserves_stage(self):
        diagnostic = app_module._watermark_evaluation_diagnostic({
            'status': 'recognized', 'image_size': [1200, 1600],
            'lines': [
                {'text': '2026-10-09 16:20', 'box': [50, 1200, 400, 1260],
                 'confidence': 0.98, 'region': 'bottom'},
                {'text': '地址：示例路88号', 'box': [50, 1270, 600, 1320],
                 'confidence': 0.97, 'region': 'bottom'},
                {'text': 'LAT:28.672656 LNG:115.869296 江西省示例市示例路88号',
                 'box': [50, 1330, 700, 1380],
                 'confidence': 0.82, 'region': 'bottom'},
                {'text': '防伪码 SECRET20261009', 'box': [50, 1390, 620, 1440],
                 'confidence': 0.96, 'region': 'bottom'},
            ],
        }, {
            'taken_at': '2026-10-09 16:20:00', 'gps_lat': 28.672656,
            'gps_lng': 115.869296, 'code': 'SECRET20261009',
            'confidence': 0.82, 'confidence_basis': 'selected_field_lines_min',
            'confidence_source_indexes': [0, 2, 3],
        }, {'qualification': 'ineligible', 'basis': 'unknown'})
        self.assertEqual((diagnostic['stage'], diagnostic['detail_code']),
                         ('watermark_confidence', 'WATERMARK_CONFIDENCE_LOW'))
        rendered = json.dumps(diagnostic, ensure_ascii=False)
        self.assertNotIn('28.672656', rendered)
        self.assertNotIn('115.869296', rendered)
        self.assertNotIn('示例路88号', rendered)
        self.assertNotIn('江西省示例市', rendered)
        self.assertNotIn('SECRET20261009', rendered)
        self.assertIn('<COORD>', rendered)
        self.assertIn('<REDACTED>', rendered)
        self.assertIn('<CODE>', rendered)

    def test_watermark_parser_does_not_cross_pair_scene_date_with_edge_watermark(self):
        payload = {
            'image_size': [1200, 1600],
            'lines': [
                {'text': '仪表日期 2025-01-03', 'box': [430, 300, 790, 360]},
                {'text': '09:26', 'box': [55, 1280, 220, 1330]},
                {'text': '2026-10-09', 'box': [250, 1280, 520, 1330]},
                {'text': '经纬度 28.071303,115.539684', 'box': [55, 1350, 620, 1400]},
                {'text': '防伪 EDGEWATERMARK123', 'box': [55, 1420, 560, 1470]},
            ],
        }
        self.assertEqual(app_module._parse_watermark_fields(payload)['taken_at'],
                         '2026-10-09 09:26:00')

    def test_watermark_recognizer_checks_top_band_and_full_image_after_bottom_band(self):
        calls = []

        class FakeOCR:
            def __call__(self, image):
                calls.append(tuple(image.shape[:2]))
                if image.shape[0] == 120 and len(calls) == 2:
                    return ([
                        [[[10, 10], [150, 10], [150, 40], [10, 40]], '09:26', 0.99],
                        [[[170, 10], [360, 10], [360, 40], [170, 40]], '2026-10-09', 0.99],
                    ], None)
                return ([], None)

        buffer = io.BytesIO()
        Image.new('RGB', (200, 300), 'white').save(buffer, format='JPEG')
        original_engine = app_module._WATERMARK_OCR
        try:
            app_module._WATERMARK_OCR = FakeOCR()
            recognized = app_module._recognize_watermark(buffer.getvalue())
        finally:
            app_module._WATERMARK_OCR = original_engine
        self.assertEqual(calls, [(120, 200), (120, 200), (300, 200)])
        self.assertEqual(recognized['status'], 'recognized')
        self.assertEqual([line['text'] for line in recognized['lines']], ['09:26', '2026-10-09'])
        self.assertEqual(recognized['image_size'], [200, 300])

    def test_watermark_recognizer_applies_exif_orientation_before_edge_ocr(self):
        calls = []

        class FakeOCR:
            def __call__(self, image):
                calls.append(tuple(image.shape[:2]))
                return ([], None)

        original_engine = app_module._WATERMARK_OCR
        try:
            app_module._WATERMARK_OCR = FakeOCR()
            recognized = app_module._recognize_watermark(
                exif_oriented_watermark_jpeg_bytes())
        finally:
            app_module._WATERMARK_OCR = original_engine
        self.assertEqual(recognized['image_size'], [900, 1600])
        self.assertEqual(calls, [(640, 900), (640, 900), (1600, 900)])

    def test_real_ocr_engine_parses_exif_oriented_labelled_watermark(self):
        recognized = self.original_recognize(exif_oriented_watermark_jpeg_bytes())
        self.assertEqual(recognized['status'], 'recognized', recognized)
        self.assertEqual(recognized['image_size'], [900, 1600])
        fields = app_module._parse_watermark_fields(recognized, include_metadata=True)
        self.assertEqual({key: fields[key] for key in ('taken_at', 'gps_lat', 'gps_lng', 'code')}, {
            'taken_at': '2026-10-09 16:20:00',
            'gps_lat': 28.672656,
            'gps_lng': 115.869296,
            'code': 'ORIENT20261009A',
        })
        self.assertGreaterEqual(fields['confidence'], app_module.EVIDENCE_WATERMARK_CONFIDENCE)

    def test_selected_watermark_confidence_ignores_unrelated_low_lines_in_same_block(self):
        lines = [
            {'text': '2026-10-09', 'box': [60, 1180, 320, 1230], 'confidence': 0.999},
            {'text': '09:26', 'box': [350, 1180, 510, 1230], 'confidence': 0.977},
            {'text': '晴 24°C', 'box': [530, 1180, 700, 1230], 'confidence': 0.20},
            {'text': '站点名称 另一处水厂', 'box': [60, 1240, 520, 1290],
             'confidence': 0.31},
            {'text': '坐标 28.071303,115.539684', 'box': [60, 1300, 620, 1350],
             'confidence': 0.953},
            {'text': '地址 江西省示例市示例路88号', 'box': [60, 1360, 680, 1410],
             'confidence': 0.24},
            {'text': '防伪 BLOCKCONFIDENCE123', 'box': [60, 1420, 600, 1470],
             'confidence': 0.995},
            {'text': '备注 现场光照较强', 'box': [60, 1480, 520, 1530],
             'confidence': 0.18},
            {'text': '品牌 WATERMARK CAMERA', 'box': [60, 1540, 580, 1590],
             'confidence': 0.27},
        ]
        fields = app_module._parse_watermark_fields({
            'text': '', 'confidence': 0.332, 'status': 'recognized',
            'image_size': [1200, 1600], 'lines': lines,
        }, include_metadata=True)
        self.assertEqual(fields['taken_at'], '2026-10-09 09:26:00')
        self.assertEqual((fields['gps_lat'], fields['gps_lng'], fields['code']),
                         (28.071303, 115.539684, 'BLOCKCONFIDENCE123'))
        self.assertEqual(fields['confidence'], 0.953)
        self.assertEqual(fields['confidence_basis'], 'selected_field_lines_min')
        self.assertEqual(fields['confidence_source_indexes'], [0, 1, 4, 6])

    def test_watermark_block_confidence_is_minimum_of_its_required_lines(self):
        base_lines = [
            {'text': '2026-10-09', 'box': [60, 1250, 320, 1300], 'confidence': 0.99},
            {'text': '09:26', 'box': [350, 1250, 510, 1300], 'confidence': 0.99},
            {'text': '坐标 28.071303,115.539684', 'box': [60, 1320, 620, 1370],
             'confidence': 0.99},
            {'text': '防伪 MINIMUMFIELD123', 'box': [60, 1390, 560, 1440],
             'confidence': 0.99},
        ]
        for weak_index in range(4):
            with self.subTest(weak_index=weak_index):
                lines = [dict(line) for line in base_lines]
                lines[weak_index]['confidence'] = 0.84
                fields = app_module._parse_watermark_fields({
                    'text': '', 'confidence': 0.95, 'status': 'recognized',
                    'image_size': [1200, 1600], 'lines': lines,
                }, include_metadata=True)
                self.assertEqual(fields['confidence'], 0.84)
                self.assertEqual(fields['confidence_source_indexes'], [0, 1, 2, 3])



    def test_invalid_upload_context_is_rejected_before_supplement_can_bind(self):
        cases = (
            ({'item_id': 999}, 'INSPECTION_ITEM_NOT_FOUND'),
            ({'plan_id': 11}, 'INSPECTION_ITEM_ACCESS_CHANGED'),
            ({'site_id': 2}, None),
        )
        for index, (overrides, expected_code) in enumerate(cases):
            with self.subTest(overrides=overrides):
                response = self.upload(
                    jpeg_bytes(('navy', 'olive', 'maroon')[index]),
                    capture_source='supplement',
                    _idempotency_key=f'invalid-supplement-context-{index}', **overrides)
                self.assertEqual(response.status_code, 403, response.json)
                if expected_code:
                    self.assertEqual(response.json['code'], expected_code)
                self.assert_no_upload_artifacts()


    def test_real_ocr_engine_parses_isolated_multiline_watermark(self):
        recognized = self.original_recognize(watermark_jpeg_bytes())
        self.assertEqual(recognized['status'], 'recognized', recognized)
        fields = app_module._parse_watermark_fields(recognized['text'])
        self.assertEqual(fields, {
            'taken_at': '2026-10-08 14:35:00',
            'gps_lat': 28.071303,
            'gps_lng': 115.539684,
            'code': 'WM20261008A123',
        })

    def test_supplement_preserves_exact_original_and_storage_relationship(self):
        original = patterned_jpeg_bytes()
        with mock.patch.object(app_module, '_recognize_watermark',
                               side_effect=AssertionError('supplement OCR must stay disabled')):
            response = self.upload(
                original, capture_source='supplement',
                _idempotency_key='preserve-exact-original')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['saved_original'])
        self.assertFalse(response.json['pending_verification'])
        self.assertEqual(response.json['code'], 'PHOTO_SAVED_AS_SUPPLEMENT')
        self.assertEqual(response.json['original_size'], len(original))
        self.assertEqual(response.json['original_sha256'], hashlib.sha256(original).hexdigest())
        self.assertTrue(response.json['display_url'].startswith(
            response.json['url'] + '?ticket='), response.json)
        with app_module.get_db() as db:
            row = db.execute("""SELECT stored_path,mime_type,file_size,sha256_hash,extra_json
                FROM operation_attachments WHERE id=?""", (response.json['id'],)).fetchone()
        display_file = os.path.join(
            self.upload_dir, row['stored_path'][len('/uploads/'):].replace('/', os.sep))
        with open(display_file, 'rb') as photo_file:
            display_data = photo_file.read()
        self.assertEqual(row['mime_type'], 'image/jpeg')
        self.assertEqual(row['file_size'], len(display_data))
        self.assertEqual(response.json['size'], len(display_data))
        self.assertEqual(row['sha256_hash'], hashlib.sha256(original).hexdigest())
        storage = json.loads(row['extra_json'])
        original_meta = storage['original_file']
        display_meta = storage['display_copy']
        self.assertTrue(original_meta['stored_path'].startswith('private://site_photo_originals/'))
        self.assertEqual(original_meta['sha256'], row['sha256_hash'])
        self.assertEqual(original_meta['size'], len(original))
        self.assertEqual(original_meta['mime_type'], 'image/jpeg')
        self.assertEqual((original_meta['format'], original_meta['width'], original_meta['height']),
                         ('JPEG', 64, 48))
        original_file = app_module._private_site_photo_target(original_meta['stored_path'])
        self.assertFalse(os.path.commonpath((self.upload_dir, original_file)) == self.upload_dir)
        with open(original_file, 'rb') as photo_file:
            self.assertEqual(photo_file.read(), original)
        self.assertEqual(display_meta['stored_path'], row['stored_path'])
        self.assertEqual(display_meta['sha256'], hashlib.sha256(display_data).hexdigest())
        self.assertEqual(display_meta['size'], len(display_data))
        self.assertEqual(display_meta['mime_type'], 'image/jpeg')
        self.assertEqual(display_meta['relationship'], 'derived_from_private_original')

        anonymous = self.client.get(f"/api/attachments/{response.json['id']}/original")
        self.assertEqual(anonymous.status_code, 401, anonymous.json)
        before_count = 1
        outsider = self.client.get(
            f"/api/attachments/{response.json['id']}/original",
            headers={'Authorization': 'Bearer outsider-token'})
        self.assertEqual(outsider.status_code, 403, outsider.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0],
                             before_count)
        restarted_client = app_module.app.test_client()
        reviewer = restarted_client.get(
            f"/api/attachments/{response.json['id']}/original",
            headers={'Authorization': 'Bearer reviewer-token'})
        self.assertEqual(reviewer.status_code, 403, reviewer.json)
        reviewer.close()
        admin = restarted_client.get(
            f"/api/attachments/{response.json['id']}/original",
            headers={'Authorization': 'Bearer admin-token'})
        self.assertEqual(admin.status_code, 403, admin.json)
        owner = restarted_client.get(
            f"/api/attachments/{response.json['id']}/original",
            headers=self.headers())
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.data, original)
        owner.close()
        guessed = self.client.get('/uploads/site_photos/' + os.path.basename(original_file))
        self.assertEqual(guessed.status_code, 403, guessed.json)
        self.assertEqual(guessed.json['code'], 'ATTACHMENT_MEDIA_TICKET_INVALID')

    def test_private_storage_precheck_fails_closed_for_missing_or_unwritable_directory(self):
        private_root = app_module._private_site_photo_root()
        self.assertFalse(os.path.exists(private_root))
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            app_module._validate_private_upload_storage()
        os.makedirs(private_root)
        with mock.patch('builtins.open', side_effect=PermissionError('read only')):
            with self.assertRaisesRegex(RuntimeError, 'not readable and writable'):
                app_module._validate_private_upload_storage()
        self.assertEqual(app_module._validate_private_upload_storage(), private_root)
        self.assertFalse(any(name.startswith('.write-probe-') for name in os.listdir(private_root)))

    def test_display_copy_removes_original_exif(self):
        original, exif_taken_at = exif_jpeg_bytes()
        self.set_valid_watermark()
        response = self.upload(original, capture_source='watermark_album',
                               _idempotency_key='strip-display-exif')
        self.assertEqual(response.status_code, 200, response.json)
        display_path = os.path.join(
            self.upload_dir, response.json['url'][len('/uploads/'):].replace('/', os.sep))
        with Image.open(display_path) as display:
            self.assertFalse(dict(display.getexif()))
        original_response = self.client.get(
            f"/api/attachments/{response.json['id']}/original", headers=self.headers())
        self.assertEqual(original_response.status_code, 200)
        with Image.open(io.BytesIO(original_response.data)) as source:
            self.assertEqual(source.getexif().get(36867), exif_taken_at)
        original_response.close()

    def test_server_watermark_uses_standard_frame_without_cropping_or_stretching(self):
        facts = {
            'site_name': '测试站',
            'item_name': '浊度仪表读数',
            'capture_time': '2026-10-10 10:20:30',
            'gps_lat': 28.071303,
            'gps_lng': 115.539684,
            'anti_counterfeit_code': 'WQ-20261010-A123',
        }
        cases = (
            ((1600, 900), (1600, 1200)),
            ((719, 1600), (1200, 1600)),
            ((280, 1200), (900, 1200)),
            ((240, 160), (240, 180)),
        )
        for size, expected_canvas in cases:
            with self.subTest(size=size):
                source = Image.new('RGB', size, 'white')
                draw = ImageDraw.Draw(source)
                draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline='red', width=2)
                buffer = io.BytesIO()
                source.save(buffer, format='PNG')
                original_bytes = buffer.getvalue()
                original_hash = hashlib.sha256(original_bytes).hexdigest()
                display_bytes, display_meta = app_module._create_site_photo_display_copy(
                    original_bytes, {'format': 'PNG'}, facts)
                self.assertEqual(hashlib.sha256(original_bytes).hexdigest(), original_hash)
                with Image.open(io.BytesIO(display_bytes)) as display:
                    self.assertEqual(display.size, expected_canvas)
                    self.assertEqual(display.width > display.height, size[0] > size[1])
                    if size[0] >= size[1]:
                        self.assertEqual(display.width * 3, display.height * 4)
                    else:
                        self.assertEqual(display.width * 4, display.height * 3)
                    content_left, content_top, content_right, content_bottom = display_meta['content_box']
                    content_width = content_right - content_left
                    content_height = content_bottom - content_top
                    self.assertAlmostEqual(content_width / content_height,
                                           size[0] / size[1], places=3)
                    self.assertEqual(display.getpixel((content_left + content_width // 2, content_top)),
                                     (255, 0, 0))
                    self.assertEqual(display.getpixel((content_right - 1,
                                                       content_top + content_height // 2)),
                                     (255, 0, 0))
                    self.assertEqual(display.getpixel((content_right - 1, content_bottom - 1)),
                                     (255, 0, 0))
                    self.assertEqual(display.getpixel((content_left,
                                                       content_top + content_height // 4)),
                                     (255, 0, 0))
                    if content_top > 0:
                        self.assertEqual(display.getpixel((display.width // 2, 0)), (32, 34, 37))
                    if content_left > 0:
                        self.assertEqual(display.getpixel((0, display.height // 2)), (32, 34, 37))
                watermark = display_meta['server_watermark']
                self.assertEqual(watermark['labels'],
                                 ['站点', '检查项', '拍摄时间', '位置坐标', '防伪码'])
                self.assertEqual(watermark['site_name'], '测试站')
                self.assertEqual(watermark['item_name'], '浊度仪表读数')
                self.assertEqual(watermark['font_name'], 'Noto Sans CJK SC')
                self.assertEqual(watermark['image_size'], list(expected_canvas))
                self.assertEqual(watermark['content_box'], display_meta['content_box'])
                left, top, right, bottom = watermark['layout_box']
                content_left, content_top, content_right, content_bottom = display_meta['content_box']
                self.assertGreaterEqual(left, content_left)
                self.assertGreaterEqual(top, content_top)
                self.assertLessEqual(right, content_right)
                self.assertLessEqual(bottom, content_bottom)
                self.assertLess(right - left, content_right - content_left)

        font = app_module._site_photo_watermark_font(24)
        glyphs = {bytes(font.getmask(char)) for char in '拍摄时间位置坐标防伪码'}
        self.assertGreaterEqual(len(glyphs), 8)

    def test_trusted_camera_frame_uses_center_cover_without_black_padding(self):
        self.assertEqual(app_module._trusted_camera_frame_contract({
            'orientation': 'portrait', 'ratio': '3:4', 'mode': 'center_cover',
        }), {'orientation': 'portrait', 'ratio': '3:4', 'mode': 'center_cover'})
        self.assertIsNone(app_module._trusted_camera_frame_contract({
            'orientation': 'landscape', 'ratio': '4:3', 'mode': 'center_cover',
        }))
        cases = (
            ((719, 1600), {'orientation': 'portrait', 'ratio': '3:4',
                           'mode': 'center_cover'}, (717, 956), [1, 322, 718, 1278]),
            ((1600, 719), {'orientation': 'portrait', 'ratio': '3:4',
                           'mode': 'center_cover'}, (537, 716), [531, 1, 1068, 717]),
        )
        for size, frame, expected_size, expected_crop in cases:
            with self.subTest(size=size):
                buffer = io.BytesIO()
                Image.new('RGB', size, (18, 140, 72)).save(buffer, format='JPEG', quality=95)
                original = buffer.getvalue()
                display_bytes, metadata = app_module._create_site_photo_display_copy(
                    original, {'format': 'JPEG'}, capture_frame=frame)
                with Image.open(io.BytesIO(display_bytes)) as display:
                    self.assertEqual(display.size, expected_size)
                    self.assertNotEqual(display.getpixel((0, 0)), (32, 34, 37))
                self.assertEqual(metadata['content_box'], [0, 0, *expected_size])
                self.assertIsNone(metadata['neutral_background'])
                self.assertEqual(metadata['capture_frame']['source_crop_box'], expected_crop)
                self.assertEqual(metadata['capture_frame']['output_size'], list(expected_size))

    def test_original_and_display_extensions_match_jpeg_png_and_webp_mime(self):
        app_module._recognize_watermark = lambda _: {
            'text': '', 'confidence': None, 'status': 'unavailable',
            'error_code': 'dependency_unavailable',
        }
        cases = (
            ('JPEG', 'red', '.jpg', 'image/jpeg'),
            ('PNG', 'green', '.png', 'image/png'),
            ('WEBP', 'blue', '.webp', 'image/webp'),
        )
        for index, (image_format, color, extension, mime_type) in enumerate(cases):
            with self.subTest(image_format=image_format):
                response = self.upload(
                    image_bytes(image_format, color), capture_source='watermark_album',
                    keep_as_supplement=True,
                    _idempotency_key=f'format-{index}')
                self.assertEqual(response.status_code, 200, response.json)
                with app_module.get_db() as db:
                    row = db.execute('SELECT stored_path,mime_type,extra_json FROM operation_attachments WHERE id=?',
                                     (response.json['id'],)).fetchone()
                extra = json.loads(row['extra_json'])
                self.assertTrue(row['stored_path'].endswith(extension))
                self.assertEqual(row['mime_type'], mime_type)
                self.assertTrue(extra['original_file']['stored_path'].endswith(extension))
                self.assertEqual(extra['original_file']['mime_type'], mime_type)
                self.assertTrue(extra['display_copy']['stored_path'].endswith(extension))
                self.assertEqual(extra['display_copy']['mime_type'], mime_type)

    def test_original_or_display_write_failure_leaves_no_rows_cache_or_files(self):
        app_module._recognize_watermark = lambda _: {
            'text': '', 'confidence': None, 'status': 'unavailable',
            'error_code': 'dependency_unavailable',
        }
        with mock.patch.object(app_module, '_create_site_photo_display_copy',
                               side_effect=OSError('injected derivative failure')):
            response = self.upload(patterned_jpeg_bytes(), capture_source='watermark_album',
                                   _idempotency_key='display-generation-failure')
        self.assertEqual(response.status_code, 500, response.json)
        self.assert_no_upload_artifacts()

        def fail_after_original(original_path, original_data, display_path, display_data):
            os.makedirs(os.path.dirname(original_path), exist_ok=True)
            with open(original_path, 'wb') as handle:
                handle.write(original_data)
            raise OSError('injected display write failure')

        with mock.patch.object(app_module, '_persist_site_photo_materials',
                               side_effect=fail_after_original):
            response = self.upload(patterned_jpeg_bytes(), capture_source='watermark_album',
                                   keep_as_supplement=True,
                                   _idempotency_key='display-write-failure')
        self.assertEqual(response.status_code, 500, response.json)
        self.assert_no_upload_artifacts()
        private_root = app_module._private_site_photo_root()
        self.assertFalse(os.path.isdir(private_root) and os.listdir(private_root))

    def test_watermark_parser_tolerates_bounded_labels_and_line_breaks_without_inventing_time(self):
        for text in (
                '2026年09月30日\n拍摄时间：16:21',
                '2026/09/30 | TIME\n16：21'):
            with self.subTest(text=text):
                self.assertEqual(
                    app_module._parse_watermark_fields(text)['taken_at'],
                    '2026-09-30 16:21:00')
        self.assertIsNone(app_module._parse_watermark_fields(
            '2026-09-30 这是一段超过十六字符的无关说明 16:21')['taken_at'])

    def test_explicit_retention_creates_supplement_only(self):
        image = jpeg_bytes('gray')
        with mock.patch.object(app_module, '_recognize_watermark',
                               side_effect=AssertionError('supplements must not run OCR')):
            response = self.upload(image, capture_source='supplement',
                                   _idempotency_key='album-supplement')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['supplemental'])
        self.assertFalse(response.json['accepted_for_review'])
        self.assertEqual(response.json['evidence_basis'], 'supplement')
        self.assertEqual(response.json['watermark_status'], 'not_requested')
        with app_module.get_db() as db:
            row = db.execute('SELECT review_required,extra_json FROM operation_attachments').fetchone()
            self.assertEqual(row['review_required'], 0)
            self.assertEqual(json.loads(row['extra_json'])['material_role'], 'supplement')
            self.assertEqual(app_module._qualified_evidence_count(db, 'inspection', 100), 0)
        reused = self.upload(image, capture_source='supplement',
                             _idempotency_key='album-supplement-reuse')
        self.assertTrue(reused.json['supplemental'])
        self.assertFalse(reused.json['accepted_for_review'])
        self.assertNotEqual(reused.json['id'], response.json['id'])
        self.assertFalse(reused.json['recovered_pending_attachment'])







    def test_client_camera_claim_without_server_session_is_rejected(self):
        response = self.upload(jpeg_bytes('red'), capture_source='camera',
                               taken_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                               gps_lat=28.071303, gps_lng=115.539684)
        self.assertFalse(response.json['accepted_for_review'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)

    def test_camera_upload_missing_trusted_client_is_rejected_without_side_effects(self):
        self.assert_camera_frame_contract_rejected(
            capture_frame_orientation='portrait',
            capture_frame_ratio='3:4',
            capture_frame_mode='center_cover',
        )

    def test_camera_upload_missing_frame_fields_is_rejected_without_side_effects(self):
        self.assert_camera_frame_contract_rejected(capture_client='trusted_camera_v1')

    def test_camera_upload_landscape_or_wrong_ratio_is_rejected_without_side_effects(self):
        invalid_frames = (
            {
                'capture_client': 'trusted_camera_v1',
                'capture_frame_orientation': 'landscape',
                'capture_frame_ratio': '4:3',
                'capture_frame_mode': 'center_cover',
            },
            {
                'capture_client': 'trusted_camera_v1',
                'capture_frame_orientation': 'portrait',
                'capture_frame_ratio': '9:16',
                'capture_frame_mode': 'center_cover',
            },
        )
        for index, metadata in enumerate(invalid_frames):
            with self.subTest(index=index, metadata=metadata):
                self.assert_camera_frame_contract_rejected(**metadata)

    def test_camera_upload_trusted_portrait_frame_is_accepted_for_review(self):
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        response = self.upload(
            portrait_jpeg_bytes('blue'),
            capture_source='camera',
            capture_session=session.json['capture_session'],
            gps_lat=28.071303,
            gps_lng=115.539684,
            _idempotency_key='trusted-portrait-frame',
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['accepted_for_review'])
        self.assertEqual(response.json['evidence_qualification'], 'qualified')
        with app_module.get_db() as db:
            attachment = db.execute(
                'SELECT extra_json FROM operation_attachments WHERE id=?',
                (response.json['id'],)).fetchone()
            capture = db.execute(
                'SELECT used_at,attachment_id FROM photo_capture_sessions WHERE token_hash=?',
                (app_module._hash_token(session.json['capture_session']),)).fetchone()
        capture_frame = json.loads(attachment['extra_json'])['capture_frame']
        self.assertEqual(capture_frame['orientation'], 'portrait')
        self.assertEqual(capture_frame['ratio'], '3:4')
        self.assertEqual(capture_frame['mode'], 'center_cover')
        self.assertIsNotNone(capture['used_at'])
        self.assertEqual(capture['attachment_id'], response.json['id'])

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
            'capture_client': 'trusted_camera_v1',
            'capture_frame_orientation': 'portrait',
            'capture_frame_ratio': '3:4',
            'capture_frame_mode': 'center_cover',
        }
        with app_module.get_db() as db:
            shifted_issued = (datetime.now() - timedelta(minutes=4)).strftime('%Y-%m-%d %H:%M:%S')
            shifted_expires = (datetime.now() + timedelta(minutes=26)).strftime('%Y-%m-%d %H:%M:%S')
            db.execute("""UPDATE photo_capture_sessions SET issued_at=?,expires_at=?
                WHERE token_hash=?""", (shifted_issued, shifted_expires,
                                          app_module._hash_token(session.json['capture_session'])))
        accepted = self.upload(portrait_jpeg_bytes('blue'), _idempotency_key='camera-1', **metadata)
        reused = self.upload(portrait_jpeg_bytes('purple'), _idempotency_key='camera-2', **metadata)
        self.assertTrue(accepted.json['accepted_for_review'])
        self.assertEqual(accepted.json['evidence_basis'], 'server_capture_session')
        self.assertEqual(
            accepted.json['watermark_code'], session.json['anti_counterfeit_code'])
        self.assertFalse(reused.json['accepted_for_review'])
        self.assertEqual(reused.json['code'], 'CAPTURE_SESSION_REQUIRED')
        with app_module.get_db() as db:
            capture = db.execute(
                'SELECT * FROM photo_capture_sessions ORDER BY id DESC LIMIT 1').fetchone()
            attachment = db.execute(
                'SELECT * FROM operation_attachments WHERE id=?',
                (accepted.json['id'],)).fetchone()
        extra = json.loads(attachment['extra_json'])
        self.assertTrue(capture['nonce'])
        self.assertEqual(capture['anti_counterfeit_code'], accepted.json['watermark_code'])
        self.assertEqual(
            (datetime.strptime(capture['expires_at'], '%Y-%m-%d %H:%M:%S')
             - datetime.strptime(capture['issued_at'], '%Y-%m-%d %H:%M:%S')).total_seconds(),
            30 * 60)
        server_watermark = extra['display_copy']['server_watermark']
        self.assertEqual(server_watermark['capture_time'], attachment['taken_at'])
        self.assertNotEqual(server_watermark['capture_time'], capture['issued_at'])
        self.assertEqual(server_watermark['site_name'], '测试站')
        self.assertEqual(server_watermark['item_name'], '浊度仪表读数')
        self.assertEqual(server_watermark['gps_lat'], capture['gps_lat'])
        self.assertEqual(server_watermark['gps_lng'], capture['gps_lng'])
        self.assertEqual(
            server_watermark['anti_counterfeit_code'], capture['anti_counterfeit_code'])
        self.assertEqual(server_watermark['labels'],
                         ['站点', '检查项', '拍摄时间', '位置坐标', '防伪码'])
        self.assertEqual(server_watermark['font_name'], 'Noto Sans CJK SC')
        self.assertEqual(len(server_watermark['layout_box']), 4)
        self.assertEqual(extra['watermark_fields']['site_name'], '测试站')
        self.assertEqual(extra['watermark_fields']['item_name'], '浊度仪表读数')
        self.assertEqual(extra['capture_frame']['ratio'], '3:4')
        self.assertIsNone(extra['display_copy']['neutral_background'])

    def test_camera_upload_uses_session_location_and_strict_idempotency(self):
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        image = patterned_jpeg_bytes()
        metadata = {
            'capture_source': 'camera',
            'capture_session': session.json['capture_session'],
            'taken_at': '2000-01-01 00:00:00',
            'gps_lat': 0, 'gps_lng': 0,
            '_idempotency_key': 'camera-strict-replay',
        }
        first = self.upload(image, **metadata)
        replay = self.upload(image, **metadata)
        conflict = self.upload(jpeg_bytes('black'), **metadata)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertTrue(first.json['accepted_for_review'])
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(replay.json, first.json)
        self.assertEqual(conflict.status_code, 409, conflict.json)
        self.assertEqual((first.json['gps_lat'], first.json['gps_lng']),
                         (28.071303, 115.539684))
        self.assertEqual(first.json['taken_at'], session.json['issued_at'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM mobile_idempotency').fetchone()[0], 1)

    def test_camera_duplicate_and_server_code_reuse_leave_new_sessions_unconsumed(self):
        first_session = self.create_capture_session()
        image = patterned_jpeg_bytes()
        first = self.upload(
            image, capture_source='camera',
            capture_session=first_session.json['capture_session'],
            gps_lat=28.071303, gps_lng=115.539684,
            _idempotency_key='camera-duplicate-first')
        self.assertTrue(first.json['accepted_for_review'], first.json)

        duplicate_session = self.create_capture_session()
        duplicate = self.upload(
            image, capture_source='camera',
            capture_session=duplicate_session.json['capture_session'],
            gps_lat=28.071303, gps_lng=115.539684,
            _idempotency_key='camera-duplicate-second')
        self.assertFalse(duplicate.json['accepted_for_review'])
        self.assertIn('重复', duplicate.json['reason'])

        code_session = self.create_capture_session()
        with app_module.get_db() as db:
            db.execute("""UPDATE photo_capture_sessions SET anti_counterfeit_code=?
                WHERE token_hash=?""", (
                first.json['watermark_code'],
                app_module._hash_token(code_session.json['capture_session'])))
        code_reuse = self.upload(
            jpeg_bytes('white'), capture_source='camera',
            capture_session=code_session.json['capture_session'],
            gps_lat=28.071303, gps_lng=115.539684,
            _idempotency_key='camera-code-reuse')
        self.assertFalse(code_reuse.json['accepted_for_review'])
        self.assertIn('重复', code_reuse.json['reason'])
        with app_module.get_db() as db:
            sessions = db.execute("""SELECT used_at,attachment_id
                FROM photo_capture_sessions ORDER BY id""").fetchall()
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 1)
        self.assertIsNotNone(sessions[0]['used_at'])
        self.assertIsNone(sessions[1]['used_at'])
        self.assertIsNone(sessions[2]['used_at'])

    def test_wrong_item_camera_photo_is_reissued_with_new_watermark_and_old_row_preserved(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,result,check_in_time,execution_status)
                VALUES (101,10,1,'余氯检查','水质检查',NULL,?,'active')""",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        image = jpeg_bytes('orchid')
        wrong_session = self.create_capture_session(item_id=100)
        wrong = self.upload(
            image, capture_source='camera',
            capture_session=wrong_session.json['capture_session'],
            gps_lat=28.071303, gps_lng=115.539684,
            _idempotency_key='wrong-item-camera')
        correct_session = self.create_capture_session(item_id=101)
        corrected = self.upload(
            image, capture_source='camera', item_id=101,
            capture_session=correct_session.json['capture_session'],
            gps_lat=28.071303, gps_lng=115.539684,
            _idempotency_key='correct-item-camera')
        self.assertTrue(wrong.json['accepted_for_review'], wrong.json)
        self.assertTrue(corrected.json['accepted_for_review'], corrected.json)
        self.assertTrue(corrected.json['recovered_pending_attachment'])
        self.assertNotEqual((wrong.json['id'], wrong.json['url'], wrong.json['watermark_code']),
                            (corrected.json['id'], corrected.json['url'],
                             corrected.json['watermark_code']))
        with app_module.get_db() as db:
            rows = db.execute("""SELECT id,item_id,review_status,evidence_qualification,extra_json
                FROM operation_attachments ORDER BY id""").fetchall()
        self.assertEqual(
            [(row['item_id'], row['review_status'], row['evidence_qualification']) for row in rows],
            [(100, 'superseded', 'ineligible'), (101, 'pending', 'qualified')])
        corrected_extra = json.loads(rows[1]['extra_json'])
        self.assertEqual(
            corrected_extra['display_copy']['server_watermark']['anti_counterfeit_code'],
            corrected.json['watermark_code'])

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
        self.assertIn('补充材料', album.json['error'])
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

    def test_prepared_capture_session_binds_location_once_without_blocking_preview(self):
        prepared = self.client.post(
            '/api/mobile/photo-capture-session', headers=self.headers(), json={
                'site_id': 1, 'plan_id': 10, 'item_id': 100,
                'capture_source': 'camera', 'prepare_only': True,
            })
        self.assertEqual(prepared.status_code, 200, prepared.json)
        self.assertFalse(prepared.json['location_verified'])
        token = prepared.json['capture_session']
        with app_module.get_db() as db:
            row = db.execute('SELECT gps_lat,gps_lng FROM photo_capture_sessions WHERE token_hash=?',
                             (app_module._hash_token(token),)).fetchone()
            self.assertEqual(tuple(row), (None, None))

        payload = {'capture_session': token, 'gps_lat': 28.071303, 'gps_lng': 115.539684}
        bound = self.client.post('/api/mobile/photo-capture-session/location',
                                 headers=self.headers(), json=payload)
        replay = self.client.post('/api/mobile/photo-capture-session/location',
                                  headers=self.headers(), json=payload)
        changed = self.client.post('/api/mobile/photo-capture-session/location',
                                   headers=self.headers(), json={
                                       'capture_session': token,
                                       'gps_lat': 28.071304, 'gps_lng': 115.539684,
                                   })
        self.assertEqual(bound.status_code, 200, bound.json)
        self.assertTrue(bound.json['location_verified'])
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(changed.status_code, 409, changed.json)
        self.assertEqual(changed.json['code'], 'CAPTURE_LOCATION_ALREADY_BOUND')
        with app_module.get_db() as db:
            row = db.execute('SELECT gps_lat,gps_lng,distance_m FROM photo_capture_sessions WHERE token_hash=?',
                             (app_module._hash_token(token),)).fetchone()
            self.assertAlmostEqual(row['gps_lat'], 28.071303)
            self.assertAlmostEqual(row['gps_lng'], 115.539684)
            self.assertLessEqual(row['distance_m'], 1)

    def test_replacement_submitted_rejects_camera_session_without_side_effects(self):
        required_at, _ = self.set_retake_required()
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET review_status=1,evidence_status='replacement_submitted' WHERE id=100""")
            before_item = db.execute("""SELECT review_status,evidence_status,rework_required_at,
                photo_urls FROM insp_plan_items WHERE id=100""").fetchone()
            before_sessions = db.execute(
                'SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0]
        response = self.create_capture_session()
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'INSPECTION_REPLACEMENT_REVIEW_PENDING')
        with app_module.get_db() as db:
            after_item = db.execute("""SELECT review_status,evidence_status,rework_required_at,
                photo_urls FROM insp_plan_items WHERE id=100""").fetchone()
            after_sessions = db.execute(
                'SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0]
        self.assertEqual(tuple(after_item), tuple(before_item))
        self.assertEqual(after_item['rework_required_at'], required_at)
        self.assertEqual((before_sessions, after_sessions), (0, 0))







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
                self.assertEqual(response.json['code'], 'CAPTURE_SESSION_REQUIRED')
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

    def test_legacy_watermark_qualified_retake_cannot_pass_submit_gate(self):
        required_at, _ = self.set_retake_required()
        verified_at = (datetime.now() + timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
        legacy_path = '/uploads/site_photos/legacy-watermark.jpg'
        with app_module.get_db() as db:
            db.execute('UPDATE insp_plan_items SET required_photos=1 WHERE id=100')
            cursor = db.execute("""INSERT INTO operation_attachments
                (filename,stored_path,source_type,source_id,plan_id,item_id,item_name,
                 site_id,uploader_id,uploader_name,gps_lat,gps_lng,taken_at,created_at,
                 category,capture_source,watermark_code,review_status,extra_json,
                 evidence_qualification,evidence_basis)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                'legacy-watermark.jpg', legacy_path, 'site_photo', 0, 10, 100,
                '浊度仪表读数', 1, 2, '现场运维', 28.071303, 115.539684,
                verified_at, verified_at, '设备检查', 'watermark_album', 'LEGACY-CODE',
                'pending', json.dumps({
                    'material_role': 'pending_inspection', 'plan_id': 10, 'item_id': 100,
                }, ensure_ascii=False), 'qualified', 'watermark'))
            attachment_id = cursor.lastrowid
            evaluation_id = db.execute("""INSERT INTO attachment_evidence_evaluations
                (attachment_id,qualification,basis,verified_taken_at,reason,next_action,
                 facts_json,evaluation_type,rule_version,actor_id,actor_name)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                attachment_id, 'qualified', 'watermark', verified_at, '历史自动评价通过', '',
                json.dumps({
                    'capture_source': 'watermark_album', 'rework_required_at': required_at,
                    'source_type': 'inspection', 'source_id': 100, 'site_id': 1,
                    'uploader_id': 2, 'distance_m': 0,
                }, ensure_ascii=False), 'automatic', 'evidence-v1', 2, '现场运维')).lastrowid
            db.execute('UPDATE operation_attachments SET evidence_evaluation_id=? WHERE id=?',
                       (evaluation_id, attachment_id))
            db.execute("""INSERT INTO photo_capture_sessions
                (token_hash,user_id,site_id,plan_id,item_id,issued_at,expires_at,
                 gps_lat,gps_lng,distance_m,rework_required_at,capture_source,nonce,
                 anti_counterfeit_code)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                'untouched-session', 2, 1, 10, 100, verified_at,
                (datetime.now() + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S'),
                28.071303, 115.539684, 0, required_at, 'camera', 'untouched-nonce',
                'WQ-UNTOUCHED'))
            before_item = dict(db.execute('SELECT * FROM insp_plan_items WHERE id=100').fetchone())
            before_attachment = dict(db.execute(
                'SELECT * FROM operation_attachments WHERE id=?', (attachment_id,)).fetchone())
            before_session = dict(db.execute(
                'SELECT * FROM photo_capture_sessions WHERE token_hash=?',
                ('untouched-session',)).fetchone())

        response = self.client.post('/api/mobile/submit-item', headers=self.headers(), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps([legacy_path]),
            '_idempotency_key': 'legacy-watermark-retake-submit',
        })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'RETAKE_EVIDENCE_CYCLE_MISMATCH')

        with app_module.get_db() as db:
            after_item = dict(db.execute('SELECT * FROM insp_plan_items WHERE id=100').fetchone())
            after_attachment = dict(db.execute(
                'SELECT * FROM operation_attachments WHERE id=?', (attachment_id,)).fetchone())
            after_session = dict(db.execute(
                'SELECT * FROM photo_capture_sessions WHERE token_hash=?',
                ('untouched-session',)).fetchone())
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM mobile_idempotency').fetchone()[0], 0)
        self.assertEqual(after_item, before_item)
        self.assertEqual(after_attachment, before_attachment)
        self.assertEqual(after_session, before_session)

    def test_consumed_server_camera_session_passes_initial_submit_gate(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plans ADD COLUMN plan_name TEXT DEFAULT '测试计划'")
            db.execute("ALTER TABLE insp_plans ADD COLUMN generate_date TEXT DEFAULT '2026-10-10'")
            db.execute('UPDATE insp_plan_items SET required_photos=1 WHERE id=100')
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        upload = self.upload(
            jpeg_bytes('teal'), capture_source='camera',
            capture_session=session.json['capture_session'],
            _idempotency_key='initial-camera-upload',
        )
        self.assertEqual(upload.status_code, 200, upload.json)
        self.assertTrue(upload.json['accepted_for_review'])
        self.assertEqual(upload.json['url'].split('?', 1)[0], upload.json['url'])
        self.assertIn('?ticket=', upload.json['display_url'])
        self.assertEqual(upload.json['id'], int(upload.json['id']))

        response = self.client.post('/api/mobile/submit-item', headers=self.headers(), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal',
            # The real client previews and submits the signed display address.
            # Persistence must normalize it back to the durable storage path.
            'photo_urls': json.dumps([upload.json['display_url']]),
            '_idempotency_key': 'initial-camera-submit',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            item = db.execute(
                'SELECT result,actual_photos,photo_urls FROM insp_plan_items WHERE id=100'
            ).fetchone()
            attachment = db.execute(
                'SELECT source_type,source_id,evidence_basis FROM operation_attachments WHERE id=?',
                (upload.json['id'],)).fetchone()
        self.assertEqual((item['result'], item['actual_photos']), ('normal', 1))
        self.assertEqual(json.loads(item['photo_urls']), [upload.json['url']])
        self.assertEqual(tuple(attachment), ('inspection', 100, 'server_capture_session'))

    def test_consumed_server_camera_session_passes_current_retake_submit_gate(self):
        required_at, _ = self.set_retake_required()
        with app_module.get_db() as db:
            db.execute('UPDATE insp_plan_items SET required_photos=1 WHERE id=100')
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        upload = self.upload(
            jpeg_bytes('navy'), capture_source='camera',
            capture_session=session.json['capture_session'],
            _idempotency_key='retake-camera-upload',
        )
        self.assertEqual(upload.status_code, 200, upload.json)
        self.assertTrue(upload.json['accepted_for_review'])

        response = self.client.post('/api/mobile/submit-item', headers=self.headers(), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps([upload.json['url']]),
            '_idempotency_key': 'retake-camera-submit',
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['evidence_status'], 'replacement_submitted')
        with app_module.get_db() as db:
            item = db.execute("""SELECT result,review_status,evidence_status,rework_required_at,
                actual_photos,photo_urls FROM insp_plan_items WHERE id=100""").fetchone()
            capture = db.execute("""SELECT used_at,attachment_id,rework_required_at
                FROM photo_capture_sessions ORDER BY id DESC LIMIT 1""").fetchone()
        self.assertEqual((item['result'], item['review_status'], item['evidence_status']),
                         ('normal', 1, 'replacement_submitted'))
        self.assertEqual(item['rework_required_at'], required_at)
        self.assertEqual((item['actual_photos'], json.loads(item['photo_urls'])),
                         (1, [upload.json['url']]))
        self.assertTrue(capture['used_at'])
        self.assertEqual(capture['attachment_id'], upload.json['id'])
        self.assertEqual(capture['rework_required_at'], required_at)



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




    def test_inspection_upload_faults_leave_no_database_rows_cache_or_orphan_file(self):
        failure_points = (
            ('evaluation', '_record_attachment_evaluation'),
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

    def test_inspection_upload_cleanup_failure_is_observable_and_rolls_back_database(self):
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        real_remove = os.remove
        cleanup_blocked = []

        def fail_first_cleanup(target):
            if not cleanup_blocked:
                cleanup_blocked.append(target)
                raise PermissionError('injected cleanup failure')
            return real_remove(target)

        with (mock.patch.object(app_module, '_record_attachment_evaluation',
                                side_effect=RuntimeError('injected persistence failure')),
              mock.patch.object(app_module.os, 'remove', side_effect=fail_first_cleanup)):
            response = self.upload(
                jpeg_bytes('teal'), capture_source='camera',
                capture_session=session.json['capture_session'],
                taken_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                gps_lat=28.071303, gps_lng=115.539684,
                _idempotency_key='inspection-cleanup-failure',
            )

        self.assertEqual(response.status_code, 500, response.json)
        self.assertEqual(response.json['code'], 'PHOTO_UPLOAD_CLEANUP_FAILED')
        self.assertEqual(len(cleanup_blocked), 1)
        self.assertTrue(os.path.exists(cleanup_blocked[0]))
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
        real_remove(cleanup_blocked[0])
        photo_dir = os.path.join(self.upload_dir, 'site_photos')
        self.assertFalse(os.path.isdir(photo_dir) and os.listdir(photo_dir))

    def test_absolute_client_url_normalizes_to_stored_path(self):
        self.assertEqual(app_module._attachment_storage_path(
            'http://127.0.0.1:5021/uploads/site_photos/a.jpg?display=1'),
            '/uploads/site_photos/a.jpg')

    def test_pending_site_photo_delete_cleans_row_and_file_with_confirmed_receipt(self):
        attachment_id, path, file_path = self.create_pending_site_photo()
        response = self.delete_pending_site_photo(path)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json, {
            'success': True, 'deleted': True, 'already_deleted': False,
            'attachment_id': attachment_id, 'path': path,
        })
        with app_module.get_db() as db:
            self.assertIsNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?',
                (attachment_id,)).fetchone())
        self.assertFalse(os.path.exists(file_path))

        replay = self.delete_pending_site_photo(path)
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(replay.json, {
            'success': True, 'deleted': False, 'already_deleted': True,
            'attachment_id': None, 'path': path,
        })

    def test_pending_inspection_site_photo_is_deletable(self):
        attachment_id, path, file_path = self.create_pending_site_photo(
            filename='pending-inspection.jpg', material_role='pending_inspection')
        response = self.delete_pending_site_photo(path)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['deleted'])
        with app_module.get_db() as db:
            self.assertIsNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?', (attachment_id,)).fetchone())
        self.assertFalse(os.path.exists(file_path))

    def test_pending_inspection_delete_removes_display_and_private_original(self):
        session = self.create_capture_session()
        self.assertEqual(session.status_code, 200, session.json)
        response = self.upload(
            patterned_jpeg_bytes(), capture_source='camera',
            capture_session=session.json['capture_session'],
            taken_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            gps_lat=28.071303, gps_lng=115.539684,
            _idempotency_key='delete-private-and-display')
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            row = db.execute('SELECT * FROM operation_attachments WHERE id=?',
                             (response.json['id'],)).fetchone()
            backups, shared = app_module._attachment_purge_physical_backups(
                db, dict(row), response.json['id'])
        self.assertFalse(shared)
        self.assertEqual(len(backups), 2)
        display_path = os.path.join(
            self.upload_dir, response.json['url'][len('/uploads/'):].replace('/', os.sep))
        original_path = app_module._attachment_private_original_target(dict(row))
        self.assertTrue(os.path.isfile(display_path))
        self.assertTrue(os.path.isfile(original_path))

        deleted = self.delete_pending_site_photo(response.json['url'])
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertFalse(os.path.exists(display_path))
        self.assertFalse(os.path.exists(original_path))
        with app_module.get_db() as db:
            self.assertIsNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?',
                (response.json['id'],)).fetchone())






    def test_pending_site_photo_delete_rejects_unsafe_or_protected_targets(self):
        cases = (
            ('other-user.jpg', 3, 'site_photo', 0),
            ('inspection.jpg', 2, 'inspection', 100),
            ('workorder.jpg', 2, 'workorder', 200),
            ('manual-report.jpg', 2, 'manual_report', 300),
        )
        for filename, uploader_id, source_type, source_id in cases:
            with self.subTest(source_type=source_type, uploader_id=uploader_id):
                attachment_id, path, file_path = self.create_pending_site_photo(
                    filename=filename, uploader_id=uploader_id,
                    source_type=source_type, source_id=source_id)
                response = self.delete_pending_site_photo(path)
                self.assertEqual(response.status_code, 404, response.json)
                with app_module.get_db() as db:
                    self.assertIsNotNone(db.execute(
                        'SELECT id FROM operation_attachments WHERE id=?',
                        (attachment_id,)).fetchone())
                self.assertTrue(os.path.exists(file_path))

        for invalid in (
                '', '/uploads/site_photos/', '/uploads/site_photos/../outside.jpg',
                '/uploads/site_photos/a.jpg?x=1', 'https://evil.example/uploads/site_photos/a.jpg'):
            with self.subTest(invalid=invalid):
                response = self.delete_pending_site_photo(invalid)
                self.assertEqual(response.status_code, 400, response.json)

    def test_pending_site_photo_file_delete_failure_keeps_row_and_file(self):
        attachment_id, path, file_path = self.create_pending_site_photo(
            filename='locked.jpg')
        real_remove = os.remove

        def fail_target(candidate):
            if os.path.normcase(candidate) == os.path.normcase(file_path):
                raise PermissionError('injected file lock')
            return real_remove(candidate)

        with mock.patch.object(app_module.os, 'remove', side_effect=fail_target):
            response = self.delete_pending_site_photo(path)
        self.assertEqual(response.status_code, 500, response.json)
        self.assertEqual(response.json['code'], 'PENDING_PHOTO_FILE_DELETE_FAILED')
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?',
                (attachment_id,)).fetchone())
        self.assertTrue(os.path.exists(file_path))

    def test_display_copy_requires_state_bound_short_lived_ticket(self):
        response = self.upload(
            patterned_jpeg_bytes(), capture_source='supplement',
            _idempotency_key='ticket-contract')
        self.assertEqual(response.status_code, 200, response.json)
        aid = response.json['id']
        stored_path = response.json['url']
        display_url = response.json['display_url']

        direct = self.client.get(stored_path)
        self.assertEqual(direct.status_code, 403, direct.json)
        served = self.client.get(display_url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers['Cache-Control'], 'private, no-store, max-age=0')
        served.close()

        parsed = urlsplit(display_url)
        ticket = parse_qs(parsed.query)['ticket'][0]
        tampered = ticket[:-1] + ('0' if ticket[-1] != '0' else '1')
        invalid = self.client.get(f'{parsed.path}?ticket={tampered}')
        self.assertEqual(invalid.status_code, 403, invalid.json)

        with app_module.get_db() as db:
            attachment = dict(db.execute(
                'SELECT * FROM operation_attachments WHERE id=?', (aid,)).fetchone())
        expired_ticket = app_module._encode_attachment_media_ticket(
            attachment,
            now_epoch=int(datetime.now().timestamp())
            - app_module.MEDIA_TICKET_TTL_SECONDS - 1)
        expired = self.client.get(f'{stored_path}?ticket={expired_ticket}')
        self.assertEqual(expired.status_code, 403, expired.json)
        self.assertEqual(expired.json['code'], 'ATTACHMENT_MEDIA_TICKET_EXPIRED')

        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET review_status='voided' WHERE id=?", (aid,))
        stale = self.client.get(display_url)
        self.assertEqual(stale.status_code, 410, stale.json)

    def test_cross_site_user_cannot_receive_display_ticket(self):
        response = self.upload(
            patterned_jpeg_bytes(), capture_source='supplement',
            _idempotency_key='cross-site-ticket')
        self.assertEqual(response.status_code, 200, response.json)
        aid = response.json['id']

        detail = self.client.get(
            f'/api/attachments/{aid}',
            headers={'Authorization': 'Bearer outsider-token'})
        self.assertEqual(detail.status_code, 403, detail.json)
        self.assertNotIn('display_url', detail.json)

        listing = self.client.get(
            '/api/attachments',
            headers={'Authorization': 'Bearer outsider-token'})
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertEqual(listing.json['items'], [])

    def test_formal_original_is_admin_only_and_success_is_audited(self):
        original = patterned_jpeg_bytes()
        response = self.upload(
            original, capture_source='supplement',
            _idempotency_key='formal-original-access')
        self.assertEqual(response.status_code, 200, response.json)
        aid = response.json['id']
        with app_module.get_db() as db:
            extra = json.loads(db.execute(
                'SELECT extra_json FROM operation_attachments WHERE id=?', (aid,)).fetchone()[0])
            extra['material_role'] = 'formal'
            db.execute("""UPDATE operation_attachments
                SET source_type='inspection',source_id=100,plan_id=10,item_id=100,extra_json=?
                WHERE id=?""", (json.dumps(extra), aid))

        for token in ('operator-token', 'reviewer-token'):
            denied = self.client.get(
                f'/api/attachments/{aid}/original',
                headers={'Authorization': f'Bearer {token}'})
            self.assertEqual(denied.status_code, 403, denied.json)
            self.assertEqual(denied.json['code'], 'ATTACHMENT_ORIGINAL_ADMIN_REQUIRED')
        cross_site = self.client.get(
            f'/api/attachments/{aid}/original',
            headers={'Authorization': 'Bearer outsider-token'})
        self.assertEqual(cross_site.status_code, 403, cross_site.json)
        allowed = self.client.get(
            f'/api/attachments/{aid}/original',
            headers={'Authorization': 'Bearer admin-token'})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.data, original)
        allowed.close()
        with app_module.get_db() as db:
            audit = db.execute("""SELECT remark FROM timeline_events
                WHERE source_type='attachment_original_access' AND source_id=?""",
                (aid,)).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(json.loads(audit['remark'])['lifecycle'], 'formal')

    def test_generic_attachment_endpoints_reject_inspection_before_file_write(self):
        for source_type in ('inspection', 'site_photo'):
            with self.subTest(endpoint='manual', source_type=source_type):
                manual = self.client.post('/api/attachments', headers=self.headers(), json={
                    'filename': 'bypass.jpg', 'stored_path': '/uploads/bypass.jpg',
                    'source_type': source_type, 'source_id': 100, 'site_id': 1,
                })
                self.assertEqual(manual.status_code, 409, manual.json)
                self.assertEqual(manual.json['code'], 'INSPECTION_UPLOAD_REQUIRES_TRUSTED_CAPTURE')

            with self.subTest(endpoint='multipart', source_type=source_type):
                upload = self.client.post('/api/upload/attachment', headers=self.headers(), data={
                    'file': (io.BytesIO(jpeg_bytes()), 'bypass.jpg'),
                    'source_type': source_type, 'source_id': '100', 'site_id': '1',
                }, content_type='multipart/form-data')
                self.assertEqual(upload.status_code, 409, upload.json)
                self.assertEqual(upload.json['code'], 'INSPECTION_UPLOAD_REQUIRES_TRUSTED_CAPTURE')

        reserved_path = self.client.post('/api/attachments', headers=self.headers(), json={
            'filename': 'alias.jpg', 'stored_path': '/uploads/site_photos/trusted.jpg',
            'source_type': 'maintenance', 'source_id': 0, 'site_id': 1,
        })
        self.assertEqual(reserved_path.status_code, 409, reserved_path.json)
        self.assertEqual(reserved_path.json['code'], 'INSPECTION_UPLOAD_REQUIRES_TRUSTED_CAPTURE')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
        attachment_dir = os.path.join(self.upload_dir, 'attachments')
        self.assertFalse(os.path.isdir(attachment_dir) and any(
            files for _, _, files in os.walk(attachment_dir)))

    def test_generic_upload_database_failure_removes_written_file(self):
        with mock.patch.object(app_module, '_record_attachment_evaluation',
                               side_effect=sqlite3.OperationalError('forced insert failure')):
            response = self.client.post('/api/upload/attachment', headers=self.headers(), data={
                'file': (io.BytesIO(jpeg_bytes()), 'maintenance.jpg'),
                'source_type': 'maintenance', 'site_id': '1',
            }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 500, response.json)
        self.assertEqual(response.json['code'], 'ATTACHMENT_UPLOAD_ROLLED_BACK')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 0)
        attachment_dir = os.path.join(self.upload_dir, 'attachments')
        self.assertFalse(os.path.isdir(attachment_dir) and any(
            files for _, _, files in os.walk(attachment_dir)))

    def test_reserved_site_photo_alias_never_receives_or_serves_ticket(self):
        aid, stored_path, _ = self.create_pending_site_photo(
            filename='reserved-alias.jpg', source_type='maintenance')
        detail = self.client.get(f'/api/attachments/{aid}', headers=self.headers())
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['display_url'], '')

        with app_module.get_db() as db:
            alias = dict(db.execute(
                'SELECT * FROM operation_attachments WHERE id=?', (aid,)).fetchone())
        ticket = app_module._encode_attachment_media_ticket(alias)
        denied = self.client.get(f'{stored_path}?ticket={ticket}')
        self.assertEqual(denied.status_code, 410, denied.json)
        self.assertEqual(denied.json['code'], 'ATTACHMENT_MEDIA_STATE_CHANGED')

    def test_supplement_withdrawal_rejects_other_user_and_submitted_item(self):
        attachment_id, _, file_path = self.create_pending_site_photo(
            filename='supplement-locked.jpg', raw_extra_json=json.dumps({
                'material_role': 'supplement', 'plan_id': 10, 'item_id': 100,
            }))
        outsider = self.client.post('/api/mobile/site-photos/delete',
            headers={'Authorization': 'Bearer outsider-token'},
            json={'attachment_id': attachment_id})
        self.assertEqual(outsider.status_code, 404, outsider.json)
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal',review_status=1 WHERE id=100")
        submitted = self.client.post('/api/mobile/site-photos/delete', headers=self.headers(),
                                     json={'attachment_id': attachment_id})
        self.assertEqual(submitted.status_code, 409, submitted.json)
        self.assertEqual(submitted.json['code'], 'SUPPLEMENT_WITHDRAWAL_WINDOW_CLOSED')
        self.assertTrue(os.path.exists(file_path))

    def test_supplement_withdrawal_rechecks_current_site_access(self):
        response = self.upload(
            patterned_jpeg_bytes(), capture_source='supplement',
            _idempotency_key='supplement-site-scope')
        self.assertEqual(response.status_code, 200, response.json)
        aid = response.json['id']
        with app_module.get_db() as db:
            row = dict(db.execute(
                'SELECT * FROM operation_attachments WHERE id=?', (aid,)).fetchone())
            db.execute('DELETE FROM user_sites WHERE user_id=2 AND site_id=1')
        display_path = app_module._attachment_upload_target(row['stored_path'])
        original_path = app_module._attachment_private_original_target(row)

        denied = self.client.post('/api/mobile/site-photos/delete', headers=self.headers(),
                                  json={'attachment_id': aid})
        self.assertEqual(denied.status_code, 409, denied.json)
        self.assertEqual(denied.json['code'], 'SUPPLEMENT_WITHDRAWAL_WINDOW_CLOSED')
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?', (aid,)).fetchone())
        self.assertTrue(os.path.exists(display_path))
        self.assertTrue(os.path.exists(original_path))

    def test_supplement_withdrawal_file_and_database_failures_restore_both_files(self):
        for label in ('file', 'database'):
            with self.subTest(label=label):
                response = self.upload(
                    patterned_jpeg_bytes(), capture_source='supplement',
                    _idempotency_key=f'supplement-withdrawal-{label}')
                self.assertEqual(response.status_code, 200, response.json)
                aid = response.json['id']
                with app_module.get_db() as db:
                    row = dict(db.execute(
                        'SELECT * FROM operation_attachments WHERE id=?', (aid,)).fetchone())
                display_path = app_module._attachment_upload_target(row['stored_path'])
                original_path = app_module._attachment_private_original_target(row)

                if label == 'file':
                    real_remove = app_module.os.remove

                    def fail_display(candidate):
                        if os.path.normcase(candidate) == os.path.normcase(display_path):
                            raise PermissionError('injected supplement file lock')
                        return real_remove(candidate)

                    patcher = mock.patch.object(app_module.os, 'remove', side_effect=fail_display)
                    expected_code = 'PENDING_PHOTO_FILE_DELETE_FAILED'
                else:
                    patcher = mock.patch.object(
                        app_module, '_attachment_purge_audit_summary',
                        side_effect=sqlite3.OperationalError('injected supplement database failure'))
                    expected_code = 'PENDING_PHOTO_DELETE_ROLLED_BACK'
                with patcher:
                    failed = self.client.post(
                        '/api/mobile/site-photos/delete', headers=self.headers(),
                        json={'attachment_id': aid})
                self.assertEqual(failed.status_code, 500, failed.json)
                self.assertEqual(failed.json['code'], expected_code)
                with app_module.get_db() as db:
                    self.assertIsNotNone(db.execute(
                        'SELECT id FROM operation_attachments WHERE id=?', (aid,)).fetchone())
                self.assertTrue(os.path.exists(display_path))
                self.assertTrue(os.path.exists(original_path))

    def test_supplement_withdrawal_reports_incomplete_restore_after_database_failure(self):
        response = self.upload(
            patterned_jpeg_bytes(), capture_source='supplement',
            _idempotency_key='supplement-withdrawal-restore-incomplete')
        self.assertEqual(response.status_code, 200, response.json)
        aid = response.json['id']
        with app_module.get_db() as db:
            row = dict(db.execute(
                'SELECT * FROM operation_attachments WHERE id=?', (aid,)).fetchone())
        display_path = app_module._attachment_upload_target(row['stored_path'])
        original_path = app_module._attachment_private_original_target(row)
        real_open = open

        def fail_display_restore(path, mode='r', *args, **kwargs):
            if (mode == 'wb'
                    and os.path.normcase(os.fspath(path)) == os.path.normcase(display_path)):
                raise PermissionError('injected supplement restore failure')
            return real_open(path, mode, *args, **kwargs)

        with mock.patch.object(
                app_module, '_attachment_purge_audit_summary',
                side_effect=sqlite3.OperationalError('injected supplement database failure')):
            with mock.patch('builtins.open', side_effect=fail_display_restore):
                failed = self.client.post(
                    '/api/mobile/site-photos/delete', headers=self.headers(),
                    json={'attachment_id': aid})

        self.assertEqual((failed.status_code, failed.json.get('code')),
                         (503, 'PENDING_PHOTO_RESTORE_INCOMPLETE'))
        self.assertTrue(failed.json['restore_incomplete'])
        self.assertEqual(failed.json['failed_file_count'], 1)
        self.assertIn('部分影像文件恢复失败', failed.json['error'])
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?', (aid,)).fetchone())
        self.assertFalse(os.path.exists(display_path))
        self.assertTrue(os.path.exists(original_path))

    def test_photo_lifecycle_retention_keeps_recent_and_used_sessions(self):
        now = datetime(2026, 10, 10, 12, 0, 0)
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO photo_capture_sessions
                (token_hash,user_id,site_id,plan_id,item_id,issued_at,expires_at,used_at,
                 capture_source,nonce,anti_counterfeit_code)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", [
                ('recent-unused', 2, 1, 10, 100, '2026-10-04 11:00:00',
                 '2026-10-04 12:00:00', None, 'camera', 'recent', 'RECENT'),
                ('old-unused', 2, 1, 10, 100, '2026-10-01 10:00:00',
                 '2026-10-01 11:00:00', None, 'camera', 'old', 'OLD'),
                ('old-used', 2, 1, 10, 100, '2026-10-01 10:00:00',
                 '2026-10-01 11:00:00', '2026-10-01 10:30:00', 'camera', 'used', 'USED'),
            ])
        old_mark = '2026-10-01 12:00:00'
        stale_id, _, stale_file = self.create_pending_site_photo(
            filename='stale-draft.jpg', raw_extra_json=json.dumps({
                'material_role': 'pending_inspection', 'plan_id': 99, 'item_id': 999,
                app_module.PHOTO_CLEANUP_CANDIDATE_KEY: old_mark,
            }))
        editable_id, _, editable_file = self.create_pending_site_photo(
            filename='editable-draft.jpg', raw_extra_json=json.dumps({
                'material_role': 'pending_inspection', 'plan_id': 10, 'item_id': 100,
                app_module.PHOTO_CLEANUP_CANDIDATE_KEY: old_mark,
            }))

        result = app_module.cleanup_photo_lifecycle(now=now)
        self.assertEqual(result['expired_unused_sessions'], 1)
        self.assertEqual(result['purged_pending_drafts'], 1)
        self.assertEqual(result['cleared_pending_marks'], 1)
        with app_module.get_db() as db:
            sessions = {row['token_hash'] for row in db.execute(
                'SELECT token_hash FROM photo_capture_sessions').fetchall()}
            self.assertEqual(sessions, {'recent-unused', 'old-used'})
            self.assertIsNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?', (stale_id,)).fetchone())
            editable = db.execute(
                'SELECT extra_json FROM operation_attachments WHERE id=?', (editable_id,)).fetchone()
        self.assertNotIn(app_module.PHOTO_CLEANUP_CANDIDATE_KEY,
                         json.loads(editable['extra_json']))
        self.assertFalse(os.path.exists(stale_file))
        self.assertTrue(os.path.exists(editable_file))

    def test_current_round_supplement_can_be_withdrawn_by_uploader(self):
        uploaded = self.upload(
            patterned_jpeg_bytes(), capture_source='supplement',
            _idempotency_key='supplement-withdraw-success')
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        attachment_id = uploaded.json['id']
        with app_module.get_db() as db:
            row = dict(db.execute(
                'SELECT * FROM operation_attachments WHERE id=?', (attachment_id,)).fetchone())
            db.execute("""INSERT INTO photo_capture_sessions
                (token_hash,user_id,site_id,plan_id,item_id,issued_at,expires_at,used_at,
                 attachment_id,capture_source,nonce,anti_counterfeit_code)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('supplement-child', 2, 1, 10, 100, '2026-10-10 00:00:00',
                 '2026-10-10 00:01:00', '2026-10-10 00:00:30', attachment_id,
                 'supplement', 'child', 'SUPPLEMENT'))
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content) VALUES (?,?,?,?,?)""",
                (2, 'photo_review', attachment_id, '旧通知', '应随附件清理'))
            db.execute("""INSERT INTO timeline_events
                (source_type,source_id,event_type,operator,remark)
                VALUES ('attachment',?,'created','现场运维','旧事件')""", (attachment_id,))
        display_path = app_module._attachment_upload_target(row['stored_path'])
        original_path = app_module._attachment_private_original_target(row)
        response = self.client.post('/api/mobile/site-photos/delete', headers=self.headers(),
                                    json={'attachment_id': attachment_id})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['deleted'])
        with app_module.get_db() as db:
            self.assertIsNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=?', (attachment_id,)).fetchone())
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM attachment_evidence_evaluations WHERE attachment_id=?',
                (attachment_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM photo_capture_sessions WHERE attachment_id=?',
                (attachment_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM notifications WHERE source_id=?',
                (attachment_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM mobile_idempotency
                WHERE json_valid(response_json)
                  AND CAST(json_extract(response_json,'$.id') AS INTEGER)=?""",
                (attachment_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='attachment' AND source_id=?""",
                (attachment_id,)).fetchone()[0], 0)
            audit = db.execute("""SELECT remark FROM timeline_events
                WHERE source_type='attachment_lifecycle_purge' AND source_id=?""",
                (attachment_id,)).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(json.loads(audit['remark'])['lifecycle'], 'supplement_withdrawal')
        self.assertFalse(os.path.exists(display_path))
        self.assertFalse(os.path.exists(original_path))

    def test_non_object_site_photo_metadata_cannot_be_physically_deleted(self):
        for index, raw_extra_json in enumerate(('[]', '["pending_inspection"]', '"legacy"')):
            with self.subTest(raw_extra_json=raw_extra_json):
                attachment_id, path, file_path = self.create_pending_site_photo(
                    filename=f'non-object-{index}.jpg', raw_extra_json=raw_extra_json)
                response = self.delete_pending_site_photo(path)
                self.assertEqual(response.status_code, 409, response.json)
                self.assertEqual(response.json['code'], 'PENDING_PHOTO_MATERIAL_PROTECTED')
                with app_module.get_db() as db:
                    self.assertIsNotNone(db.execute(
                        'SELECT id FROM operation_attachments WHERE id=?',
                        (attachment_id,)).fetchone())
                self.assertTrue(os.path.exists(file_path))


if __name__ == '__main__':
    unittest.main()
