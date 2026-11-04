import unittest

from photo_quality_history import preview, convert_local
from test_photo_quality_reuse import PhotoQualityReuseTest as Fixture


class PhotoHistoryConversionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.fixture.setUp()
        self.db = self.fixture.db
        self.db.execute("INSERT INTO insp_plans VALUES (301,NULL,'2026-09-21','active',8,NULL)")
        self.db.execute('''INSERT INTO insp_plan_items
            (id,plan_id,site_id,item_name,result,completed_at,check_out_time,
             review_status,review_comment,evidence_status,rework_required_at)
            VALUES (3001,301,1,'Meter','normal','2026-09-21 10:00:00',
                    '2026-09-21 11:00:00',3,'照片模糊','supplement_required','2026-10-01')''')
        self.db.execute('''INSERT INTO operation_attachments
            (id,source_type,source_id,item_id,plan_id,site_id,uploader_id,review_status,
             reject_reason,reviewer_id,reviewed_at,stored_path)
            VALUES (4001,'inspection',3001,3001,301,1,8,'rejected','照片模糊',1,
                    '2026-10-01 10:00:00','/original.jpg')''')
        self.db.execute("INSERT INTO vehicle_applications VALUES (11,301,'approved')")
        self.db.execute("INSERT INTO parts_requests VALUES (12,301,'approved')")
        self.db.execute("INSERT INTO work_orders VALUES (13,'in_progress')")
        self.db.commit()

    def tearDown(self):
        self.fixture.tearDown()

    def test_preview_does_not_write_and_conversion_preserves_original_and_resources(self):
        before = list(self.db.iterdump())
        result = preview(self.db)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(list(self.db.iterdump()), before)
        result = convert_local(self.db, [3001])
        self.assertEqual((result['converted_items'], result['quality_records']), (1, 1))
        self.assertEqual(tuple(self.db.execute('SELECT result,completed_at,check_out_time,review_comment,review_status FROM insp_plan_items WHERE id=3001').fetchone()),
                         ('normal', '2026-09-21 10:00:00', '2026-09-21 11:00:00', '照片模糊', 2))
        self.assertEqual(tuple(self.db.execute('SELECT stored_path,is_deleted,reject_reason FROM operation_attachments WHERE id=4001').fetchone()), ('/original.jpg', 0, '照片模糊'))
        self.assertEqual(tuple(self.db.execute('SELECT score_month,status FROM photo_quality_evaluations').fetchone()), ('2026-09', 'unqualified'))
        self.assertEqual(self.db.execute('SELECT rework_plan_id FROM vehicle_applications').fetchone()[0], 301)
        self.assertEqual(self.db.execute('SELECT plan_id FROM parts_requests').fetchone()[0], 301)
        self.assertEqual(self.db.execute('SELECT status FROM work_orders').fetchone()[0], 'in_progress')
        again = convert_local(self.db, [3001])
        self.assertEqual((again['converted_items'], again['quality_records']), (0, 0))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM photo_quality_history_audits').fetchone()[0], 1)

    def test_no_individual_basis_is_unscored_and_missing_execution_is_not_completed(self):
        self.db.execute("UPDATE operation_attachments SET reviewed_at=NULL WHERE id=4001")
        self.db.execute("UPDATE insp_plan_items SET result=NULL,completed_at=NULL,check_out_time=NULL WHERE id=3001")
        result = convert_local(self.db, [3001])
        self.assertEqual(result['quality_records'], 0)
        row = self.db.execute('SELECT result,review_status,r30_history_quality_status FROM insp_plan_items WHERE id=3001').fetchone()
        self.assertEqual(tuple(row), (None, 0, 'unscored'))
        self.assertEqual(self.db.execute('SELECT status FROM insp_plans').fetchone()[0], 'active')

    def test_other_reasons_and_resource_linked_rework_packages_require_review(self):
        self.db.execute("UPDATE insp_plan_items SET evidence_status='',review_comment='设备故障未处理' WHERE id=3001")
        self.assertEqual(len(preview(self.db)['manual_review']), 1)
        with self.assertRaises(ValueError):
            convert_local(self.db, [3001])
        self.db.execute("UPDATE insp_plan_items SET evidence_status='supplement_required' WHERE id=3001")
        self.db.execute('UPDATE insp_plans SET rework_of_plan_id=100 WHERE id=301')
        with self.assertRaises(ValueError):
            convert_local(self.db, [3001])
        self.assertEqual(self.db.execute('SELECT review_status FROM insp_plan_items').fetchone()[0], 3)

    def test_conversion_syncs_schedule_and_only_retires_matching_reminder(self):
        self.db.execute('ALTER TABLE plan_schedules ADD COLUMN field_status TEXT')
        self.db.execute("INSERT INTO plan_schedules(id,field_status) VALUES (51,'rework')")
        self.db.execute('UPDATE insp_plans SET plan_schedule_id=51 WHERE id=301')
        self.db.execute('ALTER TABLE notifications ADD COLUMN dedupe_key TEXT')
        self.db.execute("INSERT INTO notifications(source_type,source_id,dedupe_key,is_read) VALUES ('inspection_rework',301,'inspection_rework:301:1',0)")
        self.db.execute("INSERT INTO notifications(source_type,source_id,dedupe_key,is_read) VALUES ('workorder',13,'workorder:13',0)")
        convert_local(self.db, [3001])
        self.assertEqual(self.db.execute('SELECT field_status FROM plan_schedules').fetchone()[0], 'completed')
        self.assertEqual(self.db.execute("SELECT is_read FROM notifications WHERE source_type='inspection_rework'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT is_read FROM notifications WHERE source_type='workorder'").fetchone()[0], 0)
