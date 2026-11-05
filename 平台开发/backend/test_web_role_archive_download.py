import glob
import io
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
import zipfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WebRoleArchiveDownloadTest(unittest.TestCase):
    def setUp(self):
        self.db_uri = f'file:web-role-download-{uuid.uuid4().hex}?mode=memory&cache=shared'
        self.keeper = sqlite3.connect(self.db_uri, uri=True)
        self.keeper.row_factory = sqlite3.Row
        self.upload_root = tempfile.TemporaryDirectory(prefix='archive-download-test-')
        self.original_get_db = app_module.get_db
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.original_current_sql = app_module._current_attachment_archive_sql
        self.original_tokens = dict(app_module._tokens)
        self.original_site_cache = dict(app_module._site_ids_cache)
        self.original_testing = app_module.app.config.get('TESTING', False)

        @contextmanager
        def memory_db():
            db = sqlite3.connect(self.db_uri, uri=True)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        app_module.get_db = memory_db
        app_module.UPLOAD_DIR = self.upload_root.name
        app_module._current_attachment_archive_sql = (
            lambda _db, alias='oa', require_primary_file=False:
            f"(COALESCE({alias}.review_status,'pending')='approved'"
            + (f" AND {alias}.id!=3" if require_primary_file else '') + ')'
        )
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'reviewer-token': {
                'id': 1, 'role': 'reviewer', 'roles': ['reviewer'],
                'real_name': '审核员', 'username': 'reviewer',
            },
            'operator-token': {
                'id': 2, 'role': 'operator', 'roles': ['operator'],
                'real_name': '运维员', 'username': 'operator',
            },
            'empty-operator-token': {
                'id': 3, 'role': 'operator', 'roles': ['operator'],
                'real_name': '空站点运维员', 'username': 'empty-operator',
            },
        })

        with memory_db() as db:
            db.executescript('''
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, approver_id INTEGER
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY,
                    site_id INTEGER,
                    filename TEXT,
                    stored_path TEXT,
                    source_type TEXT,
                    is_deleted INTEGER DEFAULT 0,
                    review_status TEXT DEFAULT 'approved',
                    extra_json TEXT DEFAULT '',
                    item_name TEXT DEFAULT '',
                    archive_name TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    taken_at TEXT,
                    created_at TEXT,
                    evidence_qualification TEXT DEFAULT 'qualified'
                );
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO users VALUES
                    (1, '其他运维'), (2, '运维员'), (3, '空站点运维员');
                INSERT INTO sites VALUES (1, '一号站'), (2, '二号站');
                INSERT INTO plan_schedules VALUES (90, 1, NULL);
            ''')
            rows = [
                (1, 1, 'photo.jpg', '/uploads/site_photos/site1.jpg', 'inspection',
                 0, 'approved', '', '余氯检查', '', '', '现场照片',
                 '2026-10-31 08:01:02', '2026-10-31 08:02:00', 'qualified'),
                (2, 2, 'photo.jpg', '/uploads/workorder_photos/site2.jpg', 'workorder',
                 0, 'approved', '', '工单处置', '', '', '现场照片',
                 '2026-10-30 09:01:02', '2026-10-30 09:02:00', 'qualified'),
                (3, 1, 'missing.jpg', '/uploads/site_photos/missing.jpg', 'inspection',
                 0, 'approved', '', '缺失文件', '', '', '现场照片',
                 '2026-10-29 09:01:02', '2026-10-29 09:02:00', 'qualified'),
            ]
            db.executemany('''INSERT INTO operation_attachments
                (id,site_id,filename,stored_path,source_type,is_deleted,review_status,
                 extra_json,item_name,archive_name,description,category,taken_at,created_at,
                 evidence_qualification)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)

        photo_dir = os.path.join(self.upload_root.name, 'site_photos')
        os.makedirs(photo_dir, exist_ok=True)
        with open(os.path.join(photo_dir, 'site1.jpg'), 'wb') as handle:
            handle.write(b'site-one-display-copy')
        workorder_dir = os.path.join(self.upload_root.name, 'workorder_photos')
        os.makedirs(workorder_dir, exist_ok=True)
        with open(os.path.join(workorder_dir, 'site2.jpg'), 'wb') as handle:
            handle.write(b'site-two-display-copy')

        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module.UPLOAD_DIR = self.original_upload_dir
        app_module._current_attachment_archive_sql = self.original_current_sql
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        app_module.app.config['TESTING'] = self.original_testing
        self.keeper.close()
        self.upload_root.cleanup()

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def resolve(self, token, selection):
        return self.client.post(
            '/api/attachments/download/resolve',
            headers=self.headers(token),
            json={'selection': selection},
        )

    def test_empty_site_reviewer_reads_both_sites_but_operator_stays_scoped(self):
        reviewer = self.resolve('reviewer-token', {
            'mode': 'ids', 'attachment_ids': [1, 2],
        })
        self.assertEqual(reviewer.status_code, 200, reviewer.json)
        self.assertEqual([item['attachment_id'] for item in reviewer.json['items']], [1, 2])

        operator = self.resolve('operator-token', {
            'mode': 'ids', 'attachment_ids': [1, 2],
        })
        self.assertEqual(operator.status_code, 200, operator.json)
        self.assertEqual([item['attachment_id'] for item in operator.json['items']], [1])
        self.assertEqual(operator.json['failures'], [{
            'attachment_id': 2,
            'code': 'ATTACHMENT_UNAVAILABLE',
            'reason': '记录不存在或已无权访问',
        }])

        empty_operator = self.resolve('empty-operator-token', {
            'mode': 'ids', 'attachment_ids': [1, 2],
        })
        self.assertEqual(empty_operator.status_code, 200, empty_operator.json)
        self.assertEqual(empty_operator.json['items'], [])
        self.assertEqual(
            [failure['attachment_id'] for failure in empty_operator.json['failures']],
            [1, 2],
        )

        forbidden = self.client.get(
            '/api/attachments/1/download', headers=self.headers('empty-operator-token'))
        self.assertEqual((forbidden.status_code, forbidden.json['code']),
                         (404, 'ATTACHMENT_UNAVAILABLE'))

    def test_filter_selection_matches_archive_scope_and_reports_missing_files(self):
        response = self.resolve('reviewer-token', {
            'mode': 'filter', 'filters': {'archive_mode': 'current'},
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['attachment_id'] for item in response.json['items']], [1, 2])
        self.assertEqual(response.json['failures'], [])

        history = self.resolve('reviewer-token', {
            'mode': 'filter', 'filters': {'archive_mode': 'history'},
        })
        self.assertEqual(history.status_code, 200, history.json)
        self.assertEqual(history.json['items'], [])
        self.assertEqual(history.json['failures'][0]['attachment_id'], 3)
        self.assertEqual(history.json['failures'][0]['code'], 'ATTACHMENT_MEDIA_NOT_FOUND')

    def test_single_download_rechecks_current_site_scope(self):
        forbidden = self.client.get(
            '/api/attachments/2/download', headers=self.headers('operator-token'))
        self.assertEqual((forbidden.status_code, forbidden.json['code']),
                         (404, 'ATTACHMENT_UNAVAILABLE'))

        allowed = self.client.get(
            '/api/attachments/2/download', headers=self.headers('reviewer-token'))
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.data, b'site-two-display-copy')
        disposition = allowed.headers.get('Content-Disposition', '')
        self.assertIn('attachment', disposition)
        self.assertIn('2', disposition)
        allowed.close()

    def test_download_rechecks_operator_scope_after_selection(self):
        selected = self.resolve('operator-token', {
            'mode': 'ids', 'attachment_ids': [1],
        })
        self.assertEqual(selected.status_code, 200, selected.json)
        self.assertEqual([item['attachment_id'] for item in selected.json['items']], [1])

        with app_module.get_db() as db:
            db.execute('DELETE FROM user_sites WHERE user_id=2')
        app_module._site_ids_cache.clear()

        direct = self.client.get(
            '/api/attachments/1/download', headers=self.headers('operator-token'))
        self.assertEqual((direct.status_code, direct.json['code']),
                         (404, 'ATTACHMENT_UNAVAILABLE'))

        archive = self.client.post(
            '/api/attachments/download/zip',
            headers=self.headers('operator-token'),
            json={'attachment_ids': [1]},
        )
        self.assertEqual((archive.status_code, archive.json['code']),
                         (409, 'ATTACHMENT_DOWNLOAD_SELECTION_CHANGED'))
        self.assertEqual(archive.json['eligible_count'], 0)

    def test_operator_cannot_open_another_users_plan_by_direct_id(self):
        response = self.client.get(
            '/api/plan-schedules/90', headers=self.headers('operator-token'))
        self.assertEqual((response.status_code, response.json['code']),
                         (403, 'PLAN_SCHEDULE_VIEW_FORBIDDEN'))

    def test_zip_uses_site_and_date_folders_without_overwriting_duplicate_names(self):
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), 'attachment-export-*.zip')))
        response = self.client.post(
            '/api/attachments/download/zip',
            headers=self.headers('reviewer-token'),
            json={'attachment_ids': [1, 2]},
        )
        self.assertEqual(response.status_code, 200, response.get_json(silent=True))
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 2)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(any(name.startswith('一号站/2026-10-31/') for name in names))
            self.assertTrue(any(name.startswith('二号站/2026-10-30/') for name in names))
            contents = {archive.read(name) for name in names}
            self.assertEqual(contents, {b'site-one-display-copy', b'site-two-display-copy'})
        response.close()
        after = set(glob.glob(os.path.join(tempfile.gettempdir(), 'attachment-export-*.zip')))
        self.assertEqual(after, before)

    def test_zip_refuses_partial_success_when_a_file_changed(self):
        response = self.client.post(
            '/api/attachments/download/zip',
            headers=self.headers('reviewer-token'),
            json={'attachment_ids': [1, 3]},
        )
        self.assertEqual((response.status_code, response.json['code']),
                         (409, 'ATTACHMENT_DOWNLOAD_SELECTION_CHANGED'))
        self.assertEqual(response.json['eligible_count'], 1)


if __name__ == '__main__':
    unittest.main()
