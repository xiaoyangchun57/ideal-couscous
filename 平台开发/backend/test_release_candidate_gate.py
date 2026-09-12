import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'deploy' / 'verify_release_candidate.py'
MANIFEST = ROOT / 'deploy' / 'release-candidates.json'
HISTORICAL_TAGS = (
    'release-20260810-cross-module-freeze',
    'release-20260810-cross-module-freeze-r2',
    'release-20260810-cross-module-freeze-r3',
    'release-20260811-cross-module-freeze-r4',
    'release-20260811-cross-module-freeze-r5',
    'release-20260812-cross-module-freeze-r6',
    'release-20260815-cross-module-freeze-r7',
    'release-20260815-cross-module-freeze-r8',
    'release-20260817-cross-module-freeze-r9',
    'release-20260819-cross-module-freeze-r10',
    'release-20260905-cross-module-freeze-r11',
    'release-20260907-cross-module-freeze-r12',
    'release-20260907-cross-module-freeze-r13',
    'release-20260910-cross-module-freeze-r14',
    'release-20260911-cross-module-freeze-r15',
)
CURRENT_CANDIDATE = 'release-20260912-cross-module-freeze-r16'


class ReleaseCandidateGateTest(unittest.TestCase):
    def run_gate(self, tag):
        return subprocess.run(
            [sys.executable, str(SCRIPT), '--manifest', str(MANIFEST), '--tag', tag],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')

    def test_all_historical_release_tags_are_rejected(self):
        for tag in HISTORICAL_TAGS:
            with self.subTest(tag=tag):
                result = self.run_gate(tag)
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn('REJECT', output)
                self.assertIn(tag, output)

    def test_current_candidate_is_allowed_without_creating_a_tag(self):
        result = self.run_gate(CURRENT_CANDIDATE)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('ACCEPT current release candidate', output)
        self.assertIn(CURRENT_CANDIDATE, output)


if __name__ == '__main__':
    unittest.main()
