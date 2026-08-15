import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name('backup-water-monitor.sh')


def _find_bash():
    bash = shutil.which('bash')
    if bash:
        return bash
    git = shutil.which('git')
    if git:
        candidate = Path(git).resolve().parents[1] / 'bin' / 'bash.exe'
        if candidate.is_file():
            return str(candidate)
    return None


class BackupWaterMonitorScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = _find_bash()
        if not cls.bash:
            raise unittest.SkipTest('bash is not available')
        script = SCRIPT_PATH.read_text(encoding='utf-8')
        marker = 'require_directory "$APP_DIR/backend/data"'
        if marker not in script:
            raise AssertionError('backup script entry point changed')
        cls.prefix = script.split(marker, 1)[0]
        cls.probe = cls.prefix + "printf '%s\\n%s\\n' \"$APP_DIR\" \"$SNAPSHOT_ON_HOST\"\n"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / 'bin'
        self.bin_dir.mkdir()
        self.docker_log = self.root / 'docker.log'
        docker = self.bin_dir / 'docker'
        docker.write_text(
            '#!/usr/bin/env bash\n'
            'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
            'if [[ "${DOCKER_INSPECT_FAIL:-0}" == 1 ]]; then exit 42; fi\n'
            'if [[ "${1:-}" == inspect && "${2:-}" == --format ]]; then\n'
            '  printf \'%s\\n\' "${DOCKER_WORKING_DIR:-}"\n'
            'fi\n',
            encoding='utf-8',
        )
        docker.chmod(0o755)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_probe(self, *, app_dir=None, discovered=None, inspect_fail=False):
        env = os.environ.copy()
        env['PATH'] = str(self.bin_dir) + os.pathsep + env.get('PATH', '')
        env['BACKUP_DIR'] = str(self.root / 'backups')
        env['DOCKER_LOG'] = str(self.docker_log)
        env['CONTAINER_NAME'] = 'water-monitor-test'
        if app_dir is None:
            env.pop('APP_DIR', None)
        else:
            env['APP_DIR'] = app_dir
        if discovered is None:
            env.pop('DOCKER_WORKING_DIR', None)
        else:
            env['DOCKER_WORKING_DIR'] = discovered
        env['DOCKER_INSPECT_FAIL'] = '1' if inspect_fail else '0'
        return subprocess.run(
            [self.bash, '-c', self.probe],
            cwd=SCRIPT_PATH.parent,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
        )

    def test_explicit_app_dir_skips_directory_discovery(self):
        explicit = str(self.root / 'explicit-release')
        result = self.run_probe(app_dir=explicit, discovered='/opt/water-monitor-ignored')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], explicit)
        self.assertFalse(self.docker_log.exists())

    def test_discovers_compose_working_directory_before_snapshot_path(self):
        discovered = '/opt/water-monitor-20260812-r6'
        for app_dir in (None, ''):
            with self.subTest(app_dir=app_dir):
                if self.docker_log.exists():
                    self.docker_log.unlink()
                result = self.run_probe(app_dir=app_dir, discovered=discovered)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                self.assertEqual(lines[0], discovered)
                self.assertTrue(lines[1].startswith(discovered + '/backend/data/.backup-'))
                self.assertIn('inspect --format', self.docker_log.read_text(encoding='utf-8'))

    def test_missing_or_out_of_boundary_label_fails_without_backup_artifacts(self):
        for discovered in ('', '/opt/other-release', '/opt/water-monitor-../escape'):
            with self.subTest(discovered=discovered):
                result = self.run_probe(discovered=discovered)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Invalid Docker Compose working directory', result.stderr)
                backup_dir = self.root / 'backups'
                self.assertFalse(backup_dir.exists())

        result = self.run_probe(inspect_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cannot discover APP_DIR from running container', result.stderr)
        self.assertFalse((self.root / 'backups').exists())


if __name__ == '__main__':
    unittest.main()
