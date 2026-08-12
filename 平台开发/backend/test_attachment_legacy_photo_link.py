import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402


class LegacyPhotoUrlLinkTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.original_get_db = app_module.get_db

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
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, filename TEXT, stored_path TEXT, site_id INTEGER,
                    source_type TEXT, source_id INTEGER, is_deleted INTEGER DEFAULT 0
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    item_name TEXT, photo_urls TEXT
                );
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY);
            ''')
            db.executemany('INSERT INTO insp_plan_items VALUES (?,?,?,?,?)', [
                (7012, 128, 362, 'gate check', json.dumps(['http://127.0.0.1:5000/uploads/site_photos/site_362_20260807154110_c28e4aa4.jpg'])),
                (7013, 128, 362, 'station facility', json.dumps(['http://127.0.0.1:5000/uploads/site_photos/site_362_20260807153942_aa38a1f4.jpg'])),
                (7014, 128, 362, 'water intake', json.dumps(['http://127.0.0.1:5000/uploads/site_photos/site_362_20260807115135_3a1df36a.jpg'])),
                (7015, 128, 362, 'encoded path', json.dumps(['/uploads/site_photos/space%20photo.jpg?download=1'])),
                (7999, 128, 362, 'conflict', json.dumps(['http://127.0.0.1:5000/uploads/site_photos/duplicate.jpg'])),
                (8000, 128, 362, 'conflict 2', json.dumps(['http://127.0.0.1:5000/uploads/site_photos/duplicate.jpg'])),
            ])
            db.executemany('INSERT INTO operation_attachments VALUES (?,?,?,?,?,?,?)', [
                (102, 'site_362_20260807154110_c28e4aa4.jpg', '/uploads/site_photos/site_362_20260807154110_c28e4aa4.jpg', 362, 'site_photo', 0, 0),
                (101, 'site_362_20260807153942_aa38a1f4.jpg', '/uploads/site_photos/site_362_20260807153942_aa38a1f4.jpg', 362, 'site_photo', 0, 0),
                (96, 'site_362_20260807115135_3a1df36a.jpg', '/uploads/site_photos/site_362_20260807115135_3a1df36a.jpg', 362, 'site_photo', 0, 0),
                (97, 'similar.jpg', '/uploads/site_photos/similar.jpg', 362, 'site_photo', 0, 0),
                (98, 'duplicate.jpg', '/uploads/site_photos/duplicate.jpg', 362, 'site_photo', 0, 0),
                (99, 'space photo.jpg', 'https://files.example/uploads/site_photos/space%20photo.jpg?token=x', 362, 'site_photo', 0, 0),
                (100, 'existing.jpg', '/uploads/site_photos/site_362_20260807115135_3a1df36a.jpg', 362, 'site_photo', 0, 0),
                (103, 'conflicting.jpg', '/uploads/site_photos/site_362_20260807153942_aa38a1f4.jpg', 362, 'site_photo', 0, 0),
            ])

    def tearDown(self):
        app_module.get_db = self.original_get_db
        os.unlink(self.db_path)

    def test_unique_structured_urls_backfill_exact_items_idempotently(self):
        app_module.migrate_attachment_evidence_closure()
        with app_module.get_db() as db:
            values = db.execute('SELECT id, plan_id, item_id, item_name, site_id FROM operation_attachments ORDER BY id').fetchall()
            by_id = {row['id']: dict(row) for row in values}
            self.assertEqual((by_id[102]['plan_id'], by_id[102]['item_id']), (128, 7012))
            self.assertEqual((by_id[101]['plan_id'], by_id[101]['item_id']), (128, 7013))
            self.assertEqual((by_id[96]['plan_id'], by_id[96]['item_id']), (128, 7014))
            self.assertEqual(by_id[99]['item_id'], 7015)
            self.assertIsNone(by_id[97]['item_id'])
            self.assertIsNone(by_id[98]['item_id'])
            db.execute('UPDATE operation_attachments SET plan_id=128, item_id=7014, item_name="water intake" WHERE id=100')
            db.execute('UPDATE operation_attachments SET plan_id=999, item_id=7014, item_name="wrong" WHERE id=103')
        with app_module.get_db() as db:
            self.assertEqual(app_module._backfill_legacy_attachment_item_links(db), 0)
            self.assertEqual(db.execute('SELECT item_id FROM operation_attachments WHERE id=100').fetchone()[0], 7014)
            self.assertEqual(db.execute('SELECT item_id FROM operation_attachments WHERE id=103').fetchone()[0], 7014)
            self.assertEqual(db.execute("SELECT reason FROM attachment_link_migration_issues WHERE attachment_id=103").fetchone()[0], 'existing_association_conflict')
        app_module.migrate_attachment_evidence_closure()
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT item_id FROM operation_attachments WHERE id=102').fetchone()[0], 7012)


if __name__ == '__main__':
    unittest.main()
