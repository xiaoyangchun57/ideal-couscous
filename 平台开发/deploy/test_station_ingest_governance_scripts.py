import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DEPLOY_DIR = Path(__file__).resolve().parent
RAW_WRAPPER = DEPLOY_DIR / "archive-water-monitor-raw.sh"
RUNNER = DEPLOY_DIR / "run-station-ingest-retention.sh"
INSTALLER = DEPLOY_DIR / "install-station-ingest-retention-timer.sh"


def _find_bash():
    candidates = [shutil.which("bash")]
    git = shutil.which("git")
    if git:
        candidates.append(str(Path(git).resolve().parents[1] / "bin" / "bash.exe"))
    for candidate in filter(None, candidates):
        try:
            if subprocess.run([candidate, "--version"], capture_output=True).returncode == 0:
                return candidate
        except OSError:
            pass
    return None


class StationIngestGovernanceScriptsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = _find_bash()
        if not cls.bash:
            raise unittest.SkipTest("bash is not available")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        docker = self.bin_dir / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"${1:-}\" == inspect ]]; then\n"
            "  [[ \"$*\" == *Running* ]] && printf 'true\\n' || printf 'healthy\\n'\n"
            "elif [[ \"${1:-}\" == exec ]]; then\n"
            "  printf '%s\\n' \"${DOCKER_EXEC_RESULT:?}\"\n"
            "else\n"
            "  exit 64\n"
            "fi\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        python_path = subprocess.run(
            [self.bash, "-lc", 'cygpath -u "$1"', "_", sys.executable],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout.strip()
        python3 = self.bin_dir / "python3"
        python3.write_text(
            f'#!/usr/bin/env bash\nexec "{python_path}" "$@"\n',
            encoding="utf-8",
        )
        python3.chmod(0o755)
        self.env_file = self.root / "retention.env"
        self.env_file.write_text(
            "STATION_RETENTION_BATCH_LIMIT=25\n"
            "STATION_ARCHIVE_MINIMUM_TOTAL_BYTES=100\n"
            "STATION_ARCHIVE_MINIMUM_FREE_BYTES=20\n"
            "STATION_HOT_MINIMUM_TOTAL_BYTES=200\n"
            "STATION_HOT_MINIMUM_FREE_BYTES=30\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _bash_path(self, path):
        result = subprocess.run(
            [self.bash, "-lc", 'cygpath -u "$1"', "_", str(path)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _run_raw(self, payload):
        env = os.environ.copy()
        env["PATH"] = str(self.bin_dir) + os.pathsep + env.get("PATH", "")
        env["STATION_RETENTION_ENV_FILE"] = self._bash_path(self.env_file)
        env["ARCHIVE_DAY"] = "2026-10-21"
        env["DOCKER_EXEC_RESULT"] = payload
        return subprocess.run(
            [self.bash, RAW_WRAPPER.as_posix()], cwd=DEPLOY_DIR, env=env,
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_raw_wrapper_accepts_current_and_legacy_success_shapes(self):
        for payload, expected in (
            ('{"action":"archive","result":7}', 7),
            ('{"action":"archive","day":"2026-10-21","processed":3}', 3),
        ):
            with self.subTest(payload=payload):
                result = self._run_raw(payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'"processed":{expected}', result.stdout)

    def test_raw_wrapper_rejects_invalid_success_count(self):
        result = self._run_raw('{"action":"archive","result":true}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive processed count invalid", result.stderr)

    def test_raw_wrapper_requests_retry_when_batch_limit_is_reached(self):
        result = self._run_raw('{"action":"archive","result":25}')
        self.assertEqual(result.returncode, 75)
        self.assertIn('"retry_required":true', result.stdout)

    def test_runner_uses_release_overlays_and_business_database_mount(self):
        real_docker = shutil.which('docker')
        if not real_docker:
            self.skipTest('Docker Compose executable unavailable for config rendering')
        app_dir = self.root / 'release'
        app_dir.mkdir()
        (app_dir / 'docker-compose.yml').write_text('''services:
  water-monitor:
    image: test
    volumes: ["./backend/data:/app/backend/data"]
  station-ingest:
    image: test
    volumes: ["./backend/data:/app/backend/data"]
  station-ingest-retention:
    image: test
    volumes: ["./backend/data:/app/backend/data"]
''', encoding='utf-8')
        (app_dir / 'docker-compose.production.yml').write_text(
            'services:\n  water-monitor:\n    environment:\n      APP_RUNTIME_PROFILE: production\n', encoding='utf-8')
        mounts = app_dir / 'docker-compose.release-mounts.yml'
        mounts.write_text('''services:
  water-monitor:
    volumes: ["/opt/water-monitor-persistent/backend-data:/app/backend/data"]
  station-ingest:
    volumes: ["/opt/water-monitor-persistent/backend-data:/app/backend/data"]
''', encoding='utf-8')
        runner = self.root / 'runner.sh'
        # Only the immutable /opt path validation is relaxed for this temporary fixture.
        runner.write_text(RUNNER.read_text(encoding='utf-8').replace(
            '^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$', '^.+$'), encoding='utf-8')
        with self.env_file.open('a', encoding='utf-8') as stream:
            stream.write(f'APP_DIR={self._bash_path(app_dir)}\nSTATION_RETENTION_TIME_BUDGET_SECONDS=30\n')
        capture = self.root / 'captured.txt'
        docker = self.bin_dir / 'docker'
        docker.write_text('''#!/usr/bin/env bash
if [[ "$*" == *"config --format json"* ]]; then
  args=()
  for arg in "$@"; do
    [[ "$arg" == "$FIXTURE_APP"* ]] && arg="$(cygpath -w "$arg")"
    args+=("$arg")
  done
  exec "$REAL_DOCKER" "${args[@]}" --no-path-resolution
fi
printf '%s\\n' "$@" > "$CAPTURE"
printf '%s\\n' '{"action":"maintain-once","result":{"historical_maintenance_called":1,"status":"completed","retry_required":false}}'
''', encoding='utf-8')
        env = os.environ.copy()
        env.update(PATH=str(self.bin_dir)+os.pathsep+env.get('PATH',''),
                   STATION_RETENTION_ENV_FILE=self._bash_path(self.env_file),
                   REAL_DOCKER=self._bash_path(real_docker), CAPTURE=self._bash_path(capture),
                   FIXTURE_APP=self._bash_path(app_dir),
                   MSYS_NO_PATHCONV='1')
        result = subprocess.run([self.bash,runner.as_posix()],env=env,
                                capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        args = capture.read_text(encoding='utf-8').splitlines()
        self.assertEqual(args.count('-f'),3)
        self.assertIn('/opt/water-monitor-persistent/backend-data:/app/backend/data',args)
        self.assertIn('station-ingest-retention',args)
        self.assertNotIn('up',args)
        capture.unlink()
        mounts.write_text(mounts.read_text().replace(
            '  station-ingest:\n    volumes: ["/opt/water-monitor-persistent/backend-data',
            '  station-ingest:\n    volumes: ["/opt/wrong-database'),encoding='utf-8')
        rejected = subprocess.run([self.bash,runner.as_posix()],env=env,
                                  capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(rejected.returncode,0)
        self.assertFalse(capture.exists())

    def test_runner_and_installer_fail_closed_without_fake_nas_or_auto_enable(self):
        runner = RUNNER.read_text(encoding="utf-8")
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("--mirror-storage-dir", runner)
        self.assertIn("--storage-mode governance", runner)
        self.assertIn("STATION_ARCHIVE_MINIMUM_TOTAL_BYTES", runner)
        self.assertNotIn("enable --now", installer)
        self.assertIn("installed but not enabled", installer)

        env = os.environ.copy()
        env["STATION_RETENTION_ENV_FILE"] = self._bash_path(self.root / "missing.env")
        result = subprocess.run(
            [self.bash, RUNNER.as_posix()], cwd=DEPLOY_DIR, env=env,
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Retention environment is not readable", result.stderr)


if __name__ == "__main__":
    unittest.main()
