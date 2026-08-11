import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'dev_scripts' / 'check_syntax.py'


class SyntaxAggregatorTest(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=ROOT,
                              capture_output=True, text=True, encoding='utf-8', errors='replace')

    @unittest.skipUnless(shutil.which('node'), 'node is required for JavaScript syntax coverage')
    def test_node_failure_is_not_hidden_by_a_later_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_root = root / 'miniprogram'
            node_root.mkdir()
            (node_root / '01_bad.js').write_text('const = ;\n', encoding='utf-8')
            (node_root / '02_good.js').write_text('const ready = true;\n', encoding='utf-8')

            result = self.run_script('--node-root', node_root)

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('FAIL node --check', output)
        self.assertIn('01_bad.js', output)
        self.assertIn('PASS node --check', output)
        self.assertIn('02_good.js', output)

    def test_python_failure_is_not_hidden_by_a_later_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_root = root / 'backend'
            python_root.mkdir()
            (python_root / '01_bad.py').write_text('def broken(:\n    pass\n', encoding='utf-8')
            (python_root / '02_good.py').write_text('ready = True\n', encoding='utf-8')

            result = self.run_script('--python-root', python_root)

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn('FAIL py_compile', output)
        self.assertIn('01_bad.py', output)
        self.assertIn('PASS py_compile', output)
        self.assertIn('02_good.py', output)


if __name__ == '__main__':
    unittest.main()
