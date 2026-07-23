import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from inspection_rules import validate_submission_photos


class InspectionPhotoRulesTest(unittest.TestCase):
    def test_rejects_normal_submission_below_template_photo_count(self):
        self.assertEqual(validate_submission_photos('normal', 2, '["/uploads/a.jpg"]'), '现场照片不足：需拍 2 张，当前 1 张')

    def test_accepts_required_normal_and_one_photo_abnormal(self):
        self.assertIsNone(validate_submission_photos('normal', 2, '["/uploads/a.jpg", "/uploads/b.jpg"]'))
        self.assertIsNone(validate_submission_photos('abnormal', 0, '["/uploads/a.jpg"]'))


if __name__ == '__main__':
    unittest.main()
