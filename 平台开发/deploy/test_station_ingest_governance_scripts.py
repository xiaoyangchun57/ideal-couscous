import os
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DEPLOY_DIR = Path(__file__).resolve().parent
RAW_WRAPPER = DEPLOY_DIR / "archive-water-monitor-raw.sh"
RUNNER = DEPLOY_DIR / "run-station-ingest-retention.sh"
EXACT_DUPLICATE_RUNNER = DEPLOY_DIR / "run-station-exact-duplicate-cleanup.sh"
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
        runner = self.root / 'runner.sh'
        # Only the immutable /opt path validation is relaxed for this temporary fixture.
        runner.write_text(RUNNER.read_text(encoding='utf-8').replace(
            '^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$', '^.+$'), encoding='utf-8')
        with self.env_file.open('a', encoding='utf-8') as stream:
            stream.write(f'APP_DIR={self._bash_path(app_dir)}\nSTATION_RETENTION_TIME_BUDGET_SECONDS=30\n')
        legacy_retention_env = self.env_file.read_text(encoding='utf-8')
        capture = self.root / 'captured.txt'
        config_capture = self.root / 'config-captured.txt'
        docker = self.bin_dir / 'docker'
        docker.write_text('''#!/usr/bin/env bash
if [[ "$*" == *"config --format json"* ]]; then
  printf '%s\\n' "$@" > "$CONFIG_CAPTURE"
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
                   CONFIG_CAPTURE=self._bash_path(config_capture),
                   FIXTURE_APP=self._bash_path(app_dir),
                   MSYS_NO_PATHCONV='1')
        for database_source in (
                '/opt/water-monitor-persistent/backend-data',
                '/data/water-monitor/current/backend-data'):
            with self.subTest(database_source=database_source):
                mounts.write_text(f'''services:
  water-monitor:
    volumes: ["{database_source}:/app/backend/data"]
  station-ingest:
    volumes: ["{database_source}:/app/backend/data"]
''', encoding='utf-8')
                result = subprocess.run([self.bash,runner.as_posix()],env=env,
                                        capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(result.returncode,0,result.stderr)
                args = capture.read_text(encoding='utf-8').splitlines()
                config_args = config_capture.read_text(encoding='utf-8').splitlines()
                self.assertEqual(args.count('-f'),3)
                self.assertEqual(config_args.count('-f'),3)
                self.assertNotIn('--env-file', args)
                self.assertNotIn('--env-file', config_args)
                self.assertIn(f'{database_source}:/app/backend/data',args)
                self.assertIn('station-ingest-retention',args)
                self.assertNotIn('up',args)
                capture.unlink()
                config_capture.unlink()
        mounts.write_text('''services:
  water-monitor:
    volumes: ["/data/water-monitor/current/backend-data:/app/backend/data"]
  station-ingest:
    volumes: ["/opt/water-monitor-persistent/backend-data:/app/backend/data"]
''', encoding='utf-8')
        rejected = subprocess.run([self.bash,runner.as_posix()],env=env,
                                  capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(rejected.returncode,0)
        self.assertFalse(capture.exists())

        stable_overlay = app_dir / 'docker-compose.offline-cutover-current.yml'
        stable_env = app_dir / '.env.offline-cutover-current'
        stable_database_source = '/data/water-monitor/database/offline-cutover-window/current'
        stable_overlay.write_text('''services:
  water-monitor:
    image: ${CUTOVER_WEB_IMAGE:?}
    volumes: ["${CUTOVER_DB_HOST_DIR:?}:/app/backend/data"]
  station-ingest:
    image: ${CUTOVER_RECEIVER_IMAGE:?}
    volumes: ["${CUTOVER_DB_HOST_DIR:?}:/app/backend/data"]
  station-ingest-retention:
    image: ${CUTOVER_RECEIVER_IMAGE:?}
    volumes:
      - "${CUTOVER_DB_HOST_DIR:?}:/app/backend/data"
      - "${CUTOVER_SQLITE_TEMP_HOST_DIR:?}:/var/lib/station-ingest/sqlite-tmp"
''', encoding='utf-8')
        stable_env.write_text(
            f'CUTOVER_DB_HOST_DIR={stable_database_source}\n'
            'CUTOVER_SQLITE_TEMP_HOST_DIR=/data/water-monitor/governance-tmp/window\n'
            'CUTOVER_WEB_IMAGE=test-web@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'
            'CUTOVER_RECEIVER_IMAGE=test-receiver@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n',
            encoding='utf-8')
        stable_compose_file = ':'.join(self._bash_path(path) for path in (
            app_dir / 'docker-compose.yml', app_dir / 'docker-compose.production.yml',
            mounts, stable_overlay))
        self.env_file.write_text(
            legacy_retention_env
            + f'COMPOSE_FILE={stable_compose_file}\n'
            + f'COMPOSE_ENV_FILE={self._bash_path(stable_env)}\n',
            encoding='utf-8')
        stable = subprocess.run([self.bash, runner.as_posix()], env=env,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(stable.returncode, 0, stable.stderr)
        args = capture.read_text(encoding='utf-8').splitlines()
        config_args = config_capture.read_text(encoding='utf-8').splitlines()
        for captured_args in (args, config_args):
            self.assertEqual(captured_args.count('-f'), 4)
            self.assertIn('--env-file', captured_args)
            self.assertIn(self._bash_path(stable_env), captured_args)
            self.assertIn(self._bash_path(stable_overlay), captured_args)
        self.assertIn(f'{stable_database_source}:/app/backend/data', args)

        for incomplete in (
                f'COMPOSE_FILE={stable_compose_file}\n',
                f'COMPOSE_FILE={":".join(self._bash_path(path) for path in (app_dir / "docker-compose.yml", app_dir / "docker-compose.production.yml", mounts))}\n'
                f'COMPOSE_ENV_FILE={self._bash_path(stable_env)}\n'):
            capture.unlink(missing_ok=True)
            config_capture.unlink(missing_ok=True)
            self.env_file.write_text(legacy_retention_env + incomplete, encoding='utf-8')
            failed = subprocess.run([self.bash, runner.as_posix()], env=env,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('Stable retention mode requires both', failed.stderr)
            self.assertFalse(capture.exists())
            self.assertFalse(config_capture.exists())

    def test_runner_and_installer_fail_closed_without_fake_nas_or_auto_enable(self):
        runner = RUNNER.read_text(encoding="utf-8")
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("--mirror-storage-dir", runner)
        self.assertIn("--storage-mode governance", runner)
        self.assertIn("STATION_ARCHIVE_MINIMUM_TOTAL_BYTES", runner)
        self.assertIn("COMPOSE_ENV_FILE", runner)
        self.assertIn("--offline-cutover-current", installer)
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

    def test_installer_writes_legacy_or_stable_compose_contract(self):
        app_dir = self.root / 'release'
        app_dir.mkdir()
        for name in (
                'docker-compose.yml', 'docker-compose.production.yml',
                'docker-compose.release-mounts.yml',
                'docker-compose.offline-cutover-current.yml',
                '.env.offline-cutover-current'):
            (app_dir / name).write_text('fixture\n', encoding='utf-8')

        deploy_dir = self.root / 'deploy'
        deploy_dir.mkdir()
        env_output = self.root / 'installed-retention.env'
        installer = deploy_dir / INSTALLER.name
        installer_source = INSTALLER.read_text(encoding='utf-8')
        installer_source = installer_source.replace(
            'set -Eeuo pipefail\n',
            'set -Eeuo pipefail\ninstall() { :; }\nchmod() { :; }\nsystemctl() { :; }\n').replace(
            'if [[ $EUID -ne 0 ]]', 'if [[ 0 -ne 0 ]]').replace(
            '^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$', '^.+$').replace(
            '/etc/water-monitor/station-ingest-retention.env',
            self._bash_path(env_output))
        installer.write_text(installer_source, encoding='utf-8')
        installer.chmod(0o755)
        for name in (
                'run-station-ingest-retention.sh', 'archive-water-monitor-raw.sh',
                'water-monitor-station-ingest-retention.service',
                'water-monitor-station-ingest-retention.timer',
                'water-monitor-station-ingest-retention-alert@.service'):
            (deploy_dir / name).write_text('fixture\n', encoding='utf-8')

        env = os.environ.copy()
        env['PATH'] = str(self.bin_dir) + os.pathsep + env.get('PATH', '')
        base_args = [
            self.bash, installer.as_posix(), self._bash_path(app_dir),
            '500', '2700', '100', '20', '200', '30']

        legacy = subprocess.run(
            base_args, env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        legacy_contract = env_output.read_text(encoding='utf-8')
        self.assertIn('COMPOSE_FILE=', legacy_contract)
        self.assertNotIn('docker-compose.offline-cutover-current.yml', legacy_contract)
        self.assertNotIn('COMPOSE_ENV_FILE=', legacy_contract)

        stable = subprocess.run(
            base_args + ['--offline-cutover-current'], env=env,
            capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(stable.returncode, 0, stable.stderr)
        stable_contract = env_output.read_text(encoding='utf-8')
        self.assertIn('docker-compose.offline-cutover-current.yml', stable_contract)
        self.assertIn('COMPOSE_ENV_FILE=', stable_contract)
        self.assertIn('.env.offline-cutover-current', stable_contract)

    def test_exact_duplicate_runner_uses_one_scoped_maintenance_process(self):
        app_dir = self.root / "release"
        app_dir.mkdir()
        (app_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        deploy_dir = self.root / "candidate" / "deploy"
        backend_dir = self.root / "candidate" / "backend"
        deploy_dir.mkdir(parents=True)
        backend_dir.mkdir(parents=True)
        maintenance_module = backend_dir / "station_ingest_retention.py"
        source_module = DEPLOY_DIR.parent / "backend" / "station_ingest_retention.py"
        maintenance_module.write_bytes(source_module.read_bytes())
        for dependency in ("hj212_parser.py", "sl651_parser.py", "station_ingest_slots.py"):
            (backend_dir / dependency).write_bytes(
                (DEPLOY_DIR.parent / "backend" / dependency).read_bytes())
        module_sha = hashlib.sha256(maintenance_module.read_bytes()).hexdigest()
        runner = deploy_dir / "run-station-exact-duplicate-cleanup.sh"
        reviewed_runner_source = EXACT_DUPLICATE_RUNNER.read_text(encoding="utf-8")
        reviewed_sha = re.search(
            r'EXPECTED_MAINTENANCE_MODULE_SHA256="([0-9a-f]{64})"',
            reviewed_runner_source)
        self.assertIsNotNone(reviewed_sha)
        self.assertEqual(reviewed_sha.group(1), module_sha)
        runner_source = reviewed_runner_source.replace(
            "^/opt/water-monitor-[A-Za-z0-9][A-Za-z0-9._-]*$", "^.+$")
        runner_source = re.sub(
            r'EXPECTED_MAINTENANCE_MODULE_SHA256="[^"]+"',
            f'EXPECTED_MAINTENANCE_MODULE_SHA256="{module_sha}"', runner_source)
        runner.write_text(runner_source, encoding="utf-8")
        exact_env = self.root / "exact.env"
        exact_env.write_text(
            f"APP_DIR={self._bash_path(app_dir)}\n"
            "STATION_EXACT_DUPLICATE_CUTOFF=2026-10-21T00:00:00+00:00\n"
            "STATION_EXACT_DUPLICATE_RECEIPT_CUTOFF=2026-10-25T00:00:00+00:00\n"
            "STATION_EXACT_DUPLICATE_UPPER_RAW_ID=8107830\n"
            "STATION_EXACT_DUPLICATE_BATCH_LIMIT=100\n"
            "STATION_EXACT_DUPLICATE_SCAN_LIMIT=2000\n"
            "STATION_EXACT_DUPLICATE_TOTAL_TIMEOUT_SECONDS=300\n",
            encoding="utf-8",
        )
        capture = self.root / "exact-calls.txt"
        docker = self.bin_dir / "docker"
        docker.write_text('''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CAPTURE"
if [[ "$*" == *"config --format json"* ]]; then
  printf '%s\n' '{"services":{"water-monitor":{"volumes":[{"target":"/app/backend/data","type":"bind","source":"/opt/water-monitor-persistent/backend-data"}]},"station-ingest":{"volumes":[{"target":"/app/backend/data","type":"bind","source":"/opt/water-monitor-persistent/backend-data"}]}}}'
  exit 0
fi

module=""
expected_sha=""
args=("$@")
for ((index=0; index<${#args[@]}; index++)); do
  if [[ "${args[index]}" == --volume && $((index + 1)) -lt ${#args[@]} ]]; then
    volume="${args[index + 1]}"
    if [[ "$volume" == *":/app/backend/station_ingest_retention.py:ro" ]]; then
      module="${volume%:/app/backend/station_ingest_retention.py:ro}"
    fi
  elif [[ "${args[index]}" == -e && $((index + 1)) -lt ${#args[@]} ]]; then
    setting="${args[index + 1]}"
    if [[ "$setting" == EXPECTED_MAINTENANCE_MODULE_SHA256=* ]]; then
      expected_sha="${setting#*=}"
    fi
  fi
done

if [[ "$*" == *"/bin/sh -ec"* ]]; then
  [[ -n "$module" && -n "$expected_sha" ]] || exit 65
  [[ "$(sha256sum "$module" | awk '{print $1}')" == "$expected_sha" ]] || exit 66
  python3 "$(cygpath -w "$module")" --help
elif [[ -z "$module" && "$*" == *"run-exact-duplicate-cleanup"* ]]; then
  printf '%s\n' 'old image: invalid action run-exact-duplicate-cleanup' >&2
  exit 2
elif [[ "$*" == *"run-exact-duplicate-cleanup"* ]]; then
  printf '{"action":"run-exact-duplicate-cleanup","result":{"candidate_scope":"ranges-sha256:test","indexes":{"query_plans":{}},"batches":2,"result":{"cursor":7109830,"deleted":2,"complete":true},"retry_required":%s}}\n' "$CLEANUP_RETRY"
else
  exit 64
fi
''', encoding="utf-8")
        docker.chmod(0o755)
        env = os.environ.copy()
        env.update(
            PATH=str(self.bin_dir) + os.pathsep + env.get("PATH", ""),
            STATION_EXACT_DUPLICATE_ENV_FILE=self._bash_path(exact_env),
            CAPTURE=self._bash_path(capture), MSYS_NO_PATHCONV="1",
        )
        old_image = subprocess.run(
            [self.bash, docker.as_posix(), "compose", "run", "station-ingest-retention",
             "python", "-m", "backend.station_ingest_retention",
             "run-exact-duplicate-cleanup"],
            env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(old_image.returncode, 2)
        self.assertIn("old image: invalid action", old_image.stderr)
        capture.unlink()

        env["CLEANUP_RETRY"] = "true"
        partial = subprocess.run(
            [self.bash, runner.as_posix()], env=env,
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(partial.returncode, 75, partial.stderr)
        self.assertIn('"retry_required":true', partial.stdout)
        partial_calls = capture.read_text(encoding="utf-8")
        self.assertEqual(partial_calls.count("run-exact-duplicate-cleanup"), 1)
        self.assertNotIn("prepare-exact-duplicate-cleanup", partial_calls)
        self.assertNotIn("drop-exact-duplicate-cleanup-indexes", partial_calls)

        capture.unlink()
        env["CLEANUP_RETRY"] = "false"
        completed = subprocess.run(
            [self.bash, runner.as_posix()], env=env,
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        calls = capture.read_text(encoding="utf-8")
        self.assertIn(f"{self._bash_path(maintenance_module)}:/app/backend/station_ingest_retention.py:ro", calls)
        self.assertIn("/bin/sh -ec", calls)
        self.assertEqual(calls.count("run-exact-duplicate-cleanup"), 1)
        self.assertIn("--raw-storage-dir /var/lib/station-ingest/raw", calls)
        self.assertIn("--upper-raw-id 8107830", calls)
        self.assertIn("--scan-limit 2000", calls)
        for candidate_range in (
                "2000001-2002000", "4000001-4002000",
                "6000001-6002000", "7107831-7109830"):
            self.assertIn(f"--candidate-range {candidate_range}", calls)
        self.assertNotIn("prepare-exact-duplicate-cleanup", calls)
        self.assertNotIn("drop-exact-duplicate-cleanup-indexes", calls)
        self.assertNotIn("DELETE FROM", EXACT_DUPLICATE_RUNNER.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
