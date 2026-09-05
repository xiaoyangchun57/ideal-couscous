import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'dev_scripts' / 'capture-ui-validation-context.ps1'
POWERSHELL = shutil.which('powershell.exe') or shutil.which('powershell')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app as app_module  # noqa: E402


@contextmanager
def health_stub(payload, status=200):
    body = json.dumps(payload).encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class HealthRuntimeFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_health_keeps_legacy_fields_and_returns_stable_safe_source_fingerprint(self):
        first = self.client.get('/api/health').get_json()
        second = self.client.get('/api/health').get_json()
        self.assertEqual(first['status'], 'ok')
        self.assertTrue(first['time'])
        self.assertRegex(first['source_fingerprint'], r'^[0-9a-f]{64}$')
        self.assertEqual(first['source_fingerprint'], second['source_fingerprint'])
        expected = hashlib.sha256(Path(app_module.__file__).read_bytes()).hexdigest()
        self.assertEqual(first['source_fingerprint'], expected)
        self.assertEqual(
            set(first), {'status', 'time', 'runtime_profile', 'source_fingerprint'})
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn(str(ROOT), serialized)
        self.assertNotIn(app_module.DB_PATH, serialized)
        for forbidden in ('database', 'token', 'secret', 'account', 'host', 'branch', 'path'):
            self.assertNotIn(forbidden, first)

    def test_runtime_profile_contract_defaults_local_and_marks_invalid_values(self):
        self.assertEqual(app_module._resolve_runtime_profile(None), 'local')
        self.assertEqual(app_module._resolve_runtime_profile('production'), 'production')
        self.assertEqual(app_module._resolve_runtime_profile(' LOCAL '), 'local')
        self.assertEqual(app_module._resolve_runtime_profile('staging'), 'invalid')
        self.assertEqual(app_module._resolve_runtime_profile(''), 'invalid')

    def test_health_reports_the_process_runtime_profile(self):
        for profile in ('local', 'production', 'invalid'):
            with self.subTest(profile=profile), patch.object(
                    app_module, 'APP_RUNTIME_PROFILE', profile):
                payload = self.client.get('/api/health').get_json()
                self.assertEqual(payload['runtime_profile'], profile)


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell is required for this contract')
class CaptureUiValidationContextTest(unittest.TestCase):
    def run_script(self, api_url, expected='local'):
        return subprocess.run([
            POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(SCRIPT), '-ApiUrl', api_url,
            '-ExpectedProfile', expected,
        ], cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=30)

    def test_success_outputs_machine_readable_context(self):
        payload = {
            'status': 'ok', 'time': '2026-08-20T12:00:00',
            'runtime_profile': 'local', 'source_fingerprint': 'a' * 64,
        }
        with health_stub(payload) as api_url:
            result = self.run_script(api_url)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(Path(output['repository_root']), ROOT.parent)
        self.assertEqual(output['runtime_profile'], 'local')
        self.assertEqual(output['source_fingerprint'], 'a' * 64)
        self.assertEqual(output['health_status'], 'ok')
        self.assertTrue(output['branch'])
        self.assertRegex(output['head'], r'^[0-9a-f]{40}$')
        self.assertIsInstance(output['dirty_entry_count'], int)

    def test_unreachable_api_fails_with_a_clear_reason(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        result = self.run_script(f'http://127.0.0.1:{port}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('API health request failed', result.stderr)

    def test_missing_health_field_and_non_ok_status_fail(self):
        cases = [
            ({'status': 'ok', 'runtime_profile': 'local'}, 'missing required field'),
            ({'status': 'degraded', 'runtime_profile': 'local',
              'source_fingerprint': 'a' * 64}, 'health status is not ok'),
        ]
        for payload, message in cases:
            with self.subTest(message=message), health_stub(payload) as api_url:
                result = self.run_script(api_url)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)

    def test_profile_mismatch_fails(self):
        payload = {
            'status': 'ok', 'runtime_profile': 'production',
            'source_fingerprint': 'a' * 64,
        }
        with health_stub(payload) as api_url:
            result = self.run_script(api_url, expected='local')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('runtime profile mismatch', result.stderr)


if __name__ == '__main__':
    unittest.main()
