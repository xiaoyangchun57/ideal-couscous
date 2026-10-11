import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SCRIPT_PATH = Path(__file__).with_name('backup-water-monitor.sh')


def _find_bash():
    candidates = []
    bash = shutil.which('bash')
    if bash:
        candidates.append(bash)
    git = shutil.which('git')
    if git:
        candidate = Path(git).resolve().parents[1] / 'bin' / 'bash.exe'
        if candidate.is_file():
            candidates.append(str(candidate))
    for candidate in candidates:
        try:
            if subprocess.run([candidate, '--version'], capture_output=True).returncode == 0:
                return candidate
        except OSError:
            pass
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
            'elif [[ "${1:-}" == exec ]]; then\n'
            '  snapshot="$(printf \'%s\\n\' "$*" | grep -oE \'\\.backup-[0-9]{8}-[0-9]{6}\\.db\' | head -1)"\n'
            '  [[ -n "$snapshot" ]] || exit 43\n'
            '  printf \'snapshot\\n\' > "$DOCKER_APP_DIR/backend/data/$snapshot"\n'
            'fi\n',
            encoding='utf-8',
        )
        docker.chmod(0o755)

    def tearDown(self):
        self.temp_dir.cleanup()

    def bash_path(self, path):
        result = subprocess.run(
            [self.bash, '-lc', 'cygpath -u "$1"', '_', str(path)],
            capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

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

    def run_full_backup(
            self, *, include_private=True, failure_stage=None,
            include_ingest=False):
        failure_prefixes = {
            'uploads': 'uploads-',
            'private': 'private-originals-',
            'ingest': 'ingest-archives-',
        }
        if failure_stage not in (None, *failure_prefixes):
            raise ValueError(f'unsupported failure stage: {failure_stage}')
        include_ingest = include_ingest or failure_stage == 'ingest'
        app_dir = self.root / 'release'
        (app_dir / 'backend' / 'data').mkdir(parents=True)
        (app_dir / 'frontend' / 'uploads').mkdir(parents=True)
        (app_dir / 'frontend' / 'uploads' / 'display.jpg').write_bytes(b'display')
        if include_ingest:
            ingest = app_dir / 'backend' / 'data' / 'ingest-archives'
            ingest.mkdir()
            (ingest / 'raw-frame.bin').write_bytes(b'raw-frame')
        if include_private:
            private = app_dir / 'private_uploads' / 'site_photo_originals'
            private.mkdir(parents=True)
            (private / 'original.jpg').write_bytes(b'original-evidence')
        if failure_stage:
            real_tar = shutil.which('tar')
            fake_tar = self.bin_dir / 'tar'
            fake_tar.write_text(
                '#!/usr/bin/env bash\n'
                'target=""\n'
                'expect_target=0\n'
                'for arg in "$@"; do\n'
                '  if [[ "$expect_target" == 1 ]]; then target="$arg"; break; fi\n'
                '  [[ "$arg" == "-czf" ]] && expect_target=1\n'
                'done\n'
                'if [[ -n "${FAIL_ARCHIVE_PREFIX:-}" && '
                '"$(basename "$target")" == "${FAIL_ARCHIVE_PREFIX}"* ]]; then\n'
                '  printf \'partial-archive\\n\' > "$target"\n'
                '  exit 44\n'
                'fi\n'
                'exec "$REAL_TAR" "$@"\n', encoding='utf-8')
            fake_tar.chmod(0o755)
        env = os.environ.copy()
        env['PATH'] = str(self.bin_dir) + os.pathsep + env.get('PATH', '')
        env['APP_DIR'] = self.bash_path(app_dir)
        env['BACKUP_DIR'] = self.bash_path(self.root / 'backups')
        env['DOCKER_LOG'] = self.bash_path(self.docker_log)
        env['DOCKER_APP_DIR'] = self.bash_path(app_dir)
        env['CONTAINER_NAME'] = 'water-monitor-test'
        if failure_stage:
            env['REAL_TAR'] = real_tar or 'tar'
            if real_tar:
                env['REAL_TAR'] = self.bash_path(real_tar)
            env['TAR_BIN'] = self.bash_path(self.bin_dir / 'tar')
            env['FAIL_ARCHIVE_PREFIX'] = failure_prefixes[failure_stage]
        return subprocess.run(
            [self.bash, SCRIPT_PATH.as_posix()], cwd=SCRIPT_PATH.parent,
            env=env, capture_output=True, text=True, encoding='utf-8')

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

    def test_backup_script_includes_sensitive_ingest_archives_when_present(self):
        script = SCRIPT_PATH.read_text(encoding='utf-8')
        self.assertIn('INGEST_ARCHIVE_DIR=', script)
        self.assertIn('ingest-archives-${STAMP}.tar.gz', script)
        self.assertIn('PRIVATE_ORIGINALS_DIR=', script)
        self.assertIn('private-originals-${STAMP}.tar.gz', script)
        self.assertIn('planned_files=(', script)
        self.assertIn('"$INGEST_ARCHIVE_BACKUP"', script)
        self.assertIn('"$CHECKSUM_FILE"', script)
        self.assertIn('rm -f "${planned_files[@]}"', script)
        self.assertIn('sha256sum "${backup_files[@]}"', script)

    def test_full_backup_contains_private_originals_and_checksum_manifest(self):
        result = self.run_full_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Backup complete:', result.stdout)
        backups = self.root / 'backups'
        private_archive = next(backups.glob('private-originals-*.tar.gz'))
        uploads_archive = next(backups.glob('uploads-*.tar.gz'))
        checksum = next(backups.glob('SHA256SUMS-*.txt')).read_text(encoding='utf-8')
        with tarfile.open(private_archive, 'r:gz') as archive:
            self.assertIn('site_photo_originals/original.jpg', archive.getnames())
        with tarfile.open(uploads_archive, 'r:gz') as archive:
            self.assertIn('uploads/display.jpg', archive.getnames())
        self.assertIn(private_archive.name, checksum)
        self.assertIn(uploads_archive.name, checksum)
        self.assertRegex(checksum, r'water\.db-\d{8}-\d{6}')

    def test_missing_private_directory_and_partial_archive_failures_never_publish_backup_set(self):
        missing = self.run_full_backup(include_private=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('Missing directory:', missing.stderr)
        self.assertNotIn('Backup complete:', missing.stdout)

        for failure_stage in ('uploads', 'private', 'ingest'):
            self.tearDown()
            self.setUp()
            with self.subTest(failure_stage=failure_stage):
                backups = self.root / 'backups'
                backups.mkdir()
                historical = backups / 'water.db-20000101-000000'
                historical.write_bytes(b'historical-success')

                failed = self.run_full_backup(failure_stage=failure_stage)

                self.assertNotEqual(failed.returncode, 0)
                self.assertIn('Backup failed:', failed.stderr)
                self.assertNotIn('Backup complete:', failed.stdout)
                self.assertEqual(list(backups.iterdir()), [historical])
                self.assertEqual(historical.read_bytes(), b'historical-success')

    def test_private_runtime_directories_are_git_ignored(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('git is not available')
        paths = (
            '平台开发/private_uploads/site_photo_originals/example.jpg',
            '平台开发/frontend/private_uploads/site_photo_originals/example.jpg',
        )
        for path in paths:
            with self.subTest(path=path):
                result = subprocess.run(
                    [git, 'check-ignore', '-q', '--', path],
                    cwd=REPOSITORY_ROOT, capture_output=True, text=True,
                    encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_archive_preserves_lf_shebang(self):
        # The candidate archive is built from this tracked source under .gitattributes;
        # inspect it directly so the test never creates an index.lock in another worktree.
        script = SCRIPT_PATH.read_bytes()
        self.assertEqual(script.splitlines(keepends=True)[0], b'#!/usr/bin/env bash\n')
        self.assertNotIn(b'\r', script)
        attributes = (PROJECT_ROOT / '.gitattributes').read_text(encoding='utf-8')
        self.assertIn('*.sh text eol=lf', attributes)

    def test_compose_render_persists_private_originals_outside_public_uploads(self):
        docker = shutil.which('docker')
        if not docker:
            self.skipTest('docker compose is not available')
        env = os.environ.copy()
        env['SL651_RAW_STORAGE_HOST_PATH'] = (self.root / 'station-raw').as_posix()
        result = subprocess.run(
            [docker, 'compose', '-p', 'water-monitor-test', 'config'],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PRIVATE_UPLOAD_DIR: /app/private_uploads', result.stdout)
        self.assertIn('target: /app/private_uploads', result.stdout)
        self.assertIn('private_uploads', result.stdout)
        self.assertNotIn('target: /app/frontend/uploads/private', result.stdout)



if __name__ == '__main__':
    unittest.main()
