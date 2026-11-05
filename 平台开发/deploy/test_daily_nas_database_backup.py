import gzip
import importlib.util
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("daily-nas-database-backup.py")
SPEC = importlib.util.spec_from_file_location("daily_nas_database_backup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LocalWebDavHandler(BaseHTTPRequestHandler):
    root = Path()

    def log_message(self, unused_format, *unused_arguments):
        return

    def local_path(self, raw_path=None):
        path = urllib.parse.unquote(urllib.parse.urlsplit(raw_path or self.path).path)
        components = [component for component in path.split("/") if component]
        if any(component in {".", ".."} for component in components):
            raise RuntimeError("invalid test WebDAV path")
        return self.root.joinpath(*components)

    def finish_response(self, status, *, length=0):
        self.send_response(status)
        self.send_header("Content-Length", str(length))
        self.end_headers()

    def do_HEAD(self):
        target = self.local_path()
        if not target.is_file():
            self.finish_response(404)
            return
        self.finish_response(200, length=target.stat().st_size)

    def do_GET(self):
        target = self.local_path()
        if not target.is_file():
            self.finish_response(404)
            return
        payload = target.read_bytes()
        self.finish_response(200, length=len(payload))
        self.wfile.write(payload)

    def do_PUT(self):
        target = self.local_path()
        length = int(self.headers.get("Content-Length", "0"))
        target.parent.mkdir(parents=True, exist_ok=True)
        remaining = length
        with target.open("wb") as handle:
            while remaining:
                chunk = self.rfile.read(min(remaining, MODULE.CHUNK_SIZE))
                if not chunk:
                    raise RuntimeError("short test WebDAV PUT")
                handle.write(chunk)
                remaining -= len(chunk)
        self.finish_response(201)

    def do_MOVE(self):
        source = self.local_path()
        destination = self.local_path(self.headers["Destination"])
        if not source.exists():
            self.finish_response(404)
            return
        if destination.exists() and self.headers.get("Overwrite") != "T":
            self.finish_response(412)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        source.replace(destination)
        self.finish_response(201)

    def do_DELETE(self):
        target = self.local_path()
        if not target.exists():
            self.finish_response(404)
            return
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        self.finish_response(204)


class LocalWebDavClient:
    def __init__(self, server, root):
        self.protocol = "http"
        self.host, self.port = server.server_address
        self.headers = {}
        self.root = root

    def url(self, path):
        encoded = urllib.parse.quote(path, safe="/.-")
        return f"http://{self.host}:{self.port}{encoded}"

    def request(self, method, path, data=None, extra_headers=None, timeout=60):
        request = urllib.request.Request(
            self.url(path), data=data, method=method,
            headers={**self.headers, **(extra_headers or {})})
        return urllib.request.urlopen(request, timeout=timeout)

    def ensure_directory(self, parent, name=None, create=True):
        path = parent.rstrip("/") + ("/" + name if name else "")
        target = self.root.joinpath(*[part for part in path.split("/") if part])
        if create:
            target.mkdir(parents=True, exist_ok=True)
        elif not target.is_dir():
            raise RuntimeError("test WebDAV directory is missing")
        return path or "/"

    def find_directory(self, parent, name):
        path = parent.rstrip("/") + "/" + name
        target = self.root.joinpath(*[part for part in path.split("/") if part])
        return path if target.is_dir() else None

    def delete_if_present(self, path):
        try:
            with self.request("DELETE", path):
                pass
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise


class DailyNasDatabaseBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = MODULE.Settings(
            staging_dir=root / "staging",
            webdav_program=root / "webdav.py",
            webdav_config=root / "webdav.env",
            remote_path="database/daily",
            share_name="share",
            container_name="water-monitor",
            keep_generations=7,
            pending_max_hours=48,
            pending_max_attempts=3,
        )

    def tearDown(self):
        self.temp.cleanup()

    def fake_snapshot(self, settings, work_dir):
        database = work_dir / "water.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
            connection.execute("INSERT INTO sample VALUES (1)")
            connection.commit()
        return MODULE.compress_and_verify_snapshot(
            database, work_dir, created_at="2026-10-28T00:00:00+00:00")

    @staticmethod
    def fake_legacy_snapshot(settings, work_dir):
        database = work_dir / "water.db"
        database.write_bytes(b"consistent-snapshot")
        manifest = {
            "backup_kind": MODULE.BACKUP_KIND,
            "created_at": "2026-10-28T00:00:00+00:00",
            "database": {
                "filename": "water.db", "size": database.stat().st_size,
                "sha256": MODULE.sha256_file(database),
            },
            "source": "live_sqlite_backup_api",
            "verification": "test",
        }
        MODULE.atomic_json(work_dir / "manifest.json", manifest)
        return manifest

    @staticmethod
    def fake_publish(settings, state):
        state["stage"] = "rotation_complete"
        MODULE.atomic_json(settings.staging_dir / "pending.json", state)

    def test_success_removes_local_pending_after_remote_completion(self):
        with mock.patch.object(MODULE, "create_snapshot", side_effect=self.fake_snapshot), \
                mock.patch.object(MODULE, "publish_pending", side_effect=self.fake_publish):
            MODULE.run(self.settings, now=1000)
        self.assertFalse((self.settings.staging_dir / "pending.json").exists())
        self.assertEqual(list(self.settings.staging_dir.glob("pending-*")), [])

    def test_actual_snapshot_compression_webdav_fetch_restore_and_cleanup(self):
        self.settings.staging_dir.mkdir(parents=True)
        work_dir = self.settings.staging_dir / "pending-20261029-023000"
        work_dir.mkdir()
        source = Path(self.temp.name) / "source.db"
        snapshot = work_dir / "water.db"
        with closing(sqlite3.connect(source)) as source_database:
            source_database.execute(
                "CREATE TABLE readings (zero_value INTEGER NOT NULL, raw_precision TEXT NOT NULL)")
            source_database.execute("INSERT INTO readings VALUES (?, ?)", (0, "1.8050"))
            source_database.commit()
            with closing(sqlite3.connect(snapshot)) as target_database:
                source_database.backup(target_database)

        manifest = MODULE.compress_and_verify_snapshot(
            snapshot, work_dir, created_at="2026-10-29T02:30:00+00:00")
        self.assertFalse(snapshot.exists())
        self.assertFalse((work_dir / ".restored-water.db").exists())
        self.assertEqual(manifest["backup_format"], MODULE.COMPRESSED_BACKUP_FORMAT)
        self.assertEqual(manifest["database"]["size"], manifest["restore_stream"]["size"])
        self.assertEqual(manifest["database"]["sha256"], manifest["restore_stream"]["sha256"])

        generation = "20261029-023000"
        state = {
            "generation": generation, "work_dir": work_dir.name,
            "created_epoch": 1000, "attempts": 1, "stage": "snapshot_ready",
            "backup_format": MODULE.COMPRESSED_BACKUP_FORMAT,
        }
        MODULE.atomic_json(self.settings.staging_dir / "pending.json", state)
        remote_root = Path(self.temp.name) / "remote"
        (remote_root / "daily").mkdir(parents=True)
        handler = type("BoundLocalWebDavHandler", (LocalWebDavHandler,), {"root": remote_root})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = LocalWebDavClient(server, remote_root)
        try:
            with mock.patch.object(MODULE, "load_webdav", return_value=client), \
                    mock.patch.object(MODULE, "resolve_parent", return_value="/daily"), \
                    mock.patch.object(MODULE, "list_generations", return_value=[generation]):
                MODULE.publish_pending(self.settings, state)

            fetched = Path(self.temp.name) / "fetched-water.db.gz"
            with client.request("GET", f"/daily/{generation}/water.db.gz") as response:
                fetched.write_bytes(response.read())
            self.assertEqual(MODULE.sha256_file(fetched), manifest["archive"]["sha256"])
            restored = Path(self.temp.name) / "restored-water.db"
            with gzip.open(fetched, "rb") as source_archive, restored.open("wb") as target:
                shutil.copyfileobj(source_archive, target)
            MODULE.sqlite_quick_check(restored)
            with closing(sqlite3.connect(restored)) as restored_database:
                row = restored_database.execute(
                    "SELECT zero_value, raw_precision FROM readings").fetchone()
            self.assertEqual(row, (0, "1.8050"))
            receipt = json.loads(
                (remote_root / "daily" / generation / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["archive"]["filename"], "water.db.gz")
            self.assertEqual(receipt["archive"]["size"], fetched.stat().st_size)
            self.assertEqual(
                receipt["restore_stream"]["verification"],
                "streamed_gzip_bytes_match_checked_snapshot")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        MODULE.run(self.settings, now=1100)
        self.assertFalse((self.settings.staging_dir / "pending.json").exists())
        self.assertFalse(work_dir.exists())

    def test_stream_verification_failure_never_publishes(self):
        truncated = Path(self.temp.name) / "truncated-water.db.gz"
        truncated.write_bytes(gzip.compress(b"database-bytes")[:-4])
        with self.assertRaisesRegex(RuntimeError, "compressed backup is not readable"):
            MODULE.stream_restore_metadata(truncated)

        def failing_snapshot(settings, work_dir):
            snapshot = work_dir / "water.db"
            with closing(sqlite3.connect(snapshot)) as database:
                database.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
                database.execute("INSERT INTO sample VALUES (1)")
                database.commit()
            with mock.patch.object(MODULE, "stream_restore_metadata", return_value={
                    "filename": "water.db", "size": 0, "sha256": "0" * 64,
                    "verification": "streamed_gzip_bytes_match_checked_snapshot",
            }):
                return MODULE.compress_and_verify_snapshot(snapshot, work_dir)

        with mock.patch.object(MODULE, "create_snapshot", side_effect=failing_snapshot), \
                mock.patch.object(MODULE, "publish_pending") as publish:
            with self.assertRaisesRegex(
                    RuntimeError, "compressed backup does not restore the source snapshot"):
                MODULE.run(self.settings, now=1000)
        publish.assert_not_called()
        self.assertFalse((self.settings.staging_dir / "pending.json").exists())
        self.assertEqual(list(self.settings.staging_dir.glob("pending-*")), [])

    def test_failure_preserves_one_pending_and_retry_does_not_create_new_snapshot(self):
        snapshots = 0

        def snapshot(settings, work_dir):
            nonlocal snapshots
            snapshots += 1
            return self.fake_snapshot(settings, work_dir)

        with mock.patch.object(MODULE, "create_snapshot", side_effect=snapshot), \
                mock.patch.object(MODULE, "publish_pending", side_effect=RuntimeError("NAS unavailable")):
            with self.assertRaisesRegex(RuntimeError, "NAS unavailable"):
                MODULE.run(self.settings, now=1000)
        state = json.loads((self.settings.staging_dir / "pending.json").read_text(encoding="utf-8"))
        self.assertEqual(state["attempts"], 1)

        with mock.patch.object(MODULE, "create_snapshot", side_effect=snapshot), \
                mock.patch.object(MODULE, "publish_pending", side_effect=self.fake_publish):
            MODULE.run(self.settings, now=1100)
        self.assertEqual(snapshots, 1)
        self.assertFalse((self.settings.staging_dir / "pending.json").exists())

    def test_rotation_failure_resumes_without_reupload_or_false_pass(self):
        client = mock.Mock()
        client.find_directory.return_value = "/daily/.pending-generation"
        uploads = mock.Mock()
        moves = mock.Mock()
        first_generations = ["20261029-023000"] + [
            f"202610{day:02d}-023000" for day in range(28, 20, -1)]
        second_generations = first_generations[:7] + [first_generations[-1]]
        generations = mock.Mock(side_effect=[first_generations, second_generations])
        deletes = mock.Mock(side_effect=[None, RuntimeError("rotation unavailable"), None])

        def receipt_hash(unused_client, unused_path):
            state = json.loads((self.settings.staging_dir / "pending.json").read_text(encoding="utf-8"))
            return MODULE.sha256_file(
                self.settings.staging_dir / str(state["work_dir"]) / "receipt.json")

        first_output = io.StringIO()
        with mock.patch.object(MODULE, "create_snapshot", side_effect=self.fake_snapshot), \
                mock.patch.object(MODULE, "load_webdav", return_value=client), \
                mock.patch.object(MODULE, "resolve_parent", return_value="/daily"), \
                mock.patch.object(MODULE, "head_size", return_value=None) as head_size, \
                mock.patch.object(MODULE, "upload_verified", uploads), \
                mock.patch.object(MODULE, "move", moves), \
                mock.patch.object(MODULE, "remote_hash", side_effect=receipt_hash), \
                mock.patch.object(MODULE, "list_generations", generations), \
                mock.patch.object(MODULE, "delete_collection", deletes), \
                redirect_stdout(first_output):
            with self.assertRaisesRegex(RuntimeError, "rotation unavailable"):
                MODULE.run(self.settings, now=1000)

            state = json.loads(
                (self.settings.staging_dir / "pending.json").read_text(encoding="utf-8"))
            self.assertEqual(state["stage"], "remote_published")
            self.assertTrue((self.settings.staging_dir / str(state["work_dir"])).is_dir())
            self.assertNotIn("DAILY_NAS_DATABASE_BACKUP=PASS", first_output.getvalue())
            self.assertEqual(uploads.call_count, 3)
            self.assertEqual(moves.call_count, 1)

            MODULE.run(self.settings, now=1100)

        self.assertEqual(uploads.call_count, 3)
        self.assertEqual(moves.call_count, 1)
        self.assertEqual(head_size.call_count, 1)
        self.assertEqual(deletes.call_count, 3)
        self.assertEqual(
            deletes.call_args_list[-1].args[1],
            "/daily/" + first_generations[-1])
        self.assertFalse((self.settings.staging_dir / "pending.json").exists())
        self.assertEqual(list(self.settings.staging_dir.glob("pending-*")), [])

    def test_rotation_counts_only_completed_task_directories_and_keeps_seven(self):
        class Response:
            def __init__(self, status, payload=b""):
                self.status = status
                self.payload = payload
                self.headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, size=-1):
                return self.payload if size < 0 else self.payload[:size]

        valid = [f"202610{day:02d}-023000" for day in range(20, 28)]
        unknown = "20261019-023000"
        no_receipt = "20261018-023000"
        file_entry = "20261017-023000"
        entries = [(name, True) for name in valid + [unknown, no_receipt]]
        entries.append((file_entry, False))

        def response_xml():
            items = [
                '<d:response><d:href>/daily/</d:href><d:propstat><d:prop>'
                '<d:displayname>daily</d:displayname><d:resourcetype><d:collection/>'
                '</d:resourcetype></d:prop></d:propstat></d:response>'
            ]
            for name, is_directory in entries:
                resource = "<d:resourcetype><d:collection/></d:resourcetype>" if is_directory else "<d:resourcetype/>"
                items.append(
                    f'<d:response><d:href>/daily/{name}/</d:href><d:propstat><d:prop>'
                    f'<d:displayname>{name}</d:displayname>{resource}'
                    '</d:prop></d:propstat></d:response>')
            return ('<d:multistatus xmlns:d="DAV:">' + "".join(items) + '</d:multistatus>').encode()

        receipts = {}
        for name in valid[:4]:
            receipts[f"/daily/{name}/receipt.json"] = {
                "backup_kind": MODULE.BACKUP_KIND,
                "generation": name,
                "status": "complete",
                "source": "live_sqlite_backup_api",
                "database": {"filename": "water.db", "size": 123, "sha256": "a" * 64},
            }
        for name in valid[4:]:
            receipts[f"/daily/{name}/receipt.json"] = {
                "backup_kind": MODULE.BACKUP_KIND,
                "backup_format": MODULE.COMPRESSED_BACKUP_FORMAT,
                "generation": name,
                "status": "complete",
                "source": "live_sqlite_backup_api",
                "database": {
                    "filename": "water.db", "size": 123,
                    "sha256": "a" * 64, "quick_check": "ok",
                },
                "archive": {
                    "filename": "water.db.gz", "size": 45,
                    "sha256": "b" * 64, "compression": "gzip",
                },
                "restore_stream": {
                    "filename": "water.db", "size": 123,
                    "sha256": "a" * 64,
                    "verification": "streamed_gzip_bytes_match_checked_snapshot",
                },
            }
        receipts[f"/daily/{unknown}/receipt.json"] = {
            "backup_kind": "another-task", "generation": unknown, "status": "complete",
            "source": "live_sqlite_backup_api",
            "database": {"filename": "water.db", "size": 123, "sha256": "b" * 64},
        }

        deleted = []
        client = mock.Mock()

        def request(method, path, **unused):
            if method == "PROPFIND":
                return Response(207, response_xml())
            if method == "GET":
                receipt = receipts.get(path)
                return Response(200, json.dumps(receipt).encode()) if receipt else Response(404)
            if method == "DELETE":
                deleted.append(path)
                return Response(204)
            raise AssertionError((method, path))

        client.request.side_effect = request
        self.settings.staging_dir.mkdir(parents=True)
        work_dir = self.settings.staging_dir / "pending-current"
        work_dir.mkdir()
        self.fake_snapshot(self.settings, work_dir)
        state = {
            "generation": valid[-1], "work_dir": work_dir.name, "created_epoch": 1000,
            "attempts": 1, "stage": "remote_published",
            "backup_format": MODULE.COMPRESSED_BACKUP_FORMAT,
        }
        MODULE.atomic_json(self.settings.staging_dir / "pending.json", state)

        with mock.patch.object(MODULE, "load_webdav", return_value=client), \
                mock.patch.object(MODULE, "resolve_parent", return_value="/daily"):
            MODULE.publish_pending(self.settings, state)

        self.assertEqual(state["stage"], "rotation_complete")
        self.assertEqual(deleted, [f"/daily/{valid[0]}"])
        self.assertNotIn(f"/daily/{unknown}", deleted)
        self.assertNotIn(f"/daily/{no_receipt}", deleted)
        self.assertNotIn(f"/daily/{file_entry}", deleted)

    def test_old_pending_uploads_recorded_raw_format_without_reinterpretation(self):
        self.settings.staging_dir.mkdir(parents=True)
        work_dir = self.settings.staging_dir / "pending-legacy-upload"
        work_dir.mkdir()
        self.fake_legacy_snapshot(self.settings, work_dir)
        state = {
            "generation": "20261027-023000", "work_dir": work_dir.name,
            "created_epoch": 1000, "attempts": 1, "stage": "snapshot_ready",
        }
        MODULE.atomic_json(self.settings.staging_dir / "pending.json", state)
        uploads = mock.Mock()

        def receipt_hash(unused_client, unused_path):
            return MODULE.sha256_file(work_dir / "receipt.json")

        client = mock.Mock()
        client.find_directory.return_value = None
        client.ensure_directory.return_value = "/daily/.pending-20261027-023000"
        with mock.patch.object(MODULE, "load_webdav", return_value=client), \
                mock.patch.object(MODULE, "resolve_parent", return_value="/daily"), \
                mock.patch.object(MODULE, "head_size", return_value=None), \
                mock.patch.object(MODULE, "upload_verified", uploads), \
                mock.patch.object(MODULE, "move"), \
                mock.patch.object(MODULE, "remote_hash", side_effect=receipt_hash), \
                mock.patch.object(MODULE, "list_generations", return_value=[]):
            MODULE.publish_pending(self.settings, state)

        first_call = uploads.call_args_list[0]
        self.assertEqual(first_call.args[2].name, "water.db")
        self.assertFalse(first_call.kwargs["remote_hash_required"])
        self.assertEqual(state["stage"], "rotation_complete")

    def test_upload_failure_never_starts_rotation_or_deletes_successful_generations(self):
        self.settings.staging_dir.mkdir(parents=True)
        work_dir = self.settings.staging_dir / "pending-new-failure"
        work_dir.mkdir()
        self.fake_snapshot(self.settings, work_dir)
        state = {
            "generation": "20261029-023000", "work_dir": work_dir.name,
            "created_epoch": 1000, "attempts": 1, "stage": "snapshot_ready",
            "backup_format": MODULE.COMPRESSED_BACKUP_FORMAT,
        }
        MODULE.atomic_json(self.settings.staging_dir / "pending.json", state)
        client = mock.Mock()
        client.find_directory.return_value = "/daily/.pending-20261029-023000"
        with mock.patch.object(MODULE, "load_webdav", return_value=client), \
                mock.patch.object(MODULE, "resolve_parent", return_value="/daily"), \
                mock.patch.object(MODULE, "head_size", return_value=None), \
                mock.patch.object(MODULE, "upload_verified", side_effect=RuntimeError("upload failed")), \
                mock.patch.object(MODULE, "list_generations") as generations, \
                mock.patch.object(MODULE, "delete_collection") as delete:
            with self.assertRaisesRegex(RuntimeError, "upload failed"):
                MODULE.publish_pending(self.settings, state)
        generations.assert_not_called()
        delete.assert_not_called()
        self.assertEqual(state["stage"], "snapshot_ready")
        self.assertTrue((work_dir / "water.db.gz").is_file())

    def test_legacy_remote_complete_state_only_resumes_rotation(self):
        self.settings.staging_dir.mkdir(parents=True)
        work_dir = self.settings.staging_dir / "pending-legacy"
        work_dir.mkdir()
        manifest = self.fake_legacy_snapshot(self.settings, work_dir)
        legacy_receipt = dict(manifest)
        legacy_receipt.pop("backup_kind")
        legacy_receipt.update(
            generation="20261027-023000", status="complete",
            verification="local_quick_check_sha256_remote_size_and_receipt_before_atomic_publish")
        MODULE.atomic_json(work_dir / "receipt.json", legacy_receipt)
        state = {
            "generation": "20261027-023000", "work_dir": work_dir.name,
            "created_epoch": 1000, "attempts": 2, "stage": "remote_complete",
        }
        MODULE.atomic_json(self.settings.staging_dir / "pending.json", state)

        with mock.patch.object(MODULE, "load_webdav", return_value=mock.Mock()), \
                mock.patch.object(MODULE, "resolve_parent", return_value="/daily"), \
                mock.patch.object(MODULE, "list_generations", return_value=[]), \
                mock.patch.object(MODULE, "head_size") as head_size, \
                mock.patch.object(MODULE, "upload_verified") as upload, \
                mock.patch.object(MODULE, "remote_hash") as remote_hash:
            MODULE.publish_pending(self.settings, state)

        self.assertEqual(state["stage"], "rotation_complete")
        head_size.assert_not_called()
        upload.assert_not_called()
        remote_hash.assert_not_called()
        persisted_receipt = json.loads((work_dir / "receipt.json").read_text(encoding="utf-8"))
        self.assertNotIn("backup_kind", persisted_receipt)

    def test_stale_pending_is_abandoned_before_new_generation(self):
        self.settings.staging_dir.mkdir(parents=True)
        old_dir = self.settings.staging_dir / "pending-old"
        old_dir.mkdir()
        MODULE.atomic_json(self.settings.staging_dir / "pending.json", {
            "generation": "20261020-000000", "work_dir": old_dir.name,
            "created_epoch": 1, "attempts": 3, "stage": "snapshot_ready",
        })
        with mock.patch.object(MODULE, "load_webdav", side_effect=RuntimeError("offline")), \
                mock.patch.object(MODULE, "create_snapshot", side_effect=self.fake_snapshot), \
                mock.patch.object(MODULE, "publish_pending", side_effect=self.fake_publish):
            MODULE.run(self.settings, now=200000)
        self.assertFalse(old_dir.exists())
        self.assertFalse((self.settings.staging_dir / "pending.json").exists())

    def test_timer_and_installer_do_not_auto_enable(self):
        timer = Path(__file__).with_name("water-monitor-daily-nas-database-backup.timer").read_text(encoding="utf-8")
        installer = Path(__file__).with_name("install-daily-nas-database-backup.sh").read_text(encoding="utf-8")
        wrapper = Path(__file__).with_name("archive-water-monitor-raw.sh").read_text(encoding="utf-8")
        self.assertIn("02:30:00", timer)
        self.assertNotIn("enable --now", installer)
        self.assertIn("ARCHIVE_WRAPPER_CONTRACT=action-result-v2", wrapper)
        self.assertIn("sha256sum /usr/local/sbin/archive-water-monitor-raw", installer)

    def test_settings_require_root_only_webdav_credentials(self):
        program = Path(self.temp.name) / "webdav.py"
        credentials = Path(self.temp.name) / "webdav.env"
        config = Path(self.temp.name) / "daily.env"
        program.write_text("# test\n", encoding="utf-8")
        credentials.write_text("PASSWORD=secret\n", encoding="utf-8")
        credentials.chmod(0o644)
        config.write_text(
            f"STAGING_DIR={self.settings.staging_dir}\n"
            f"WEBDAV_PROGRAM={program}\n"
            f"WEBDAV_CONFIG={credentials}\n"
            "NAS_REMOTE_PATH=database/daily\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "root-owned and root-only"):
            MODULE.Settings.read(config)

    def test_large_database_upload_uses_size_and_atomic_move_without_full_readback(self):
        source = Path(self.temp.name) / "water.db"
        source.write_bytes(b"database-bytes")
        client = mock.Mock()
        with mock.patch.object(MODULE, "head_size", side_effect=[None, source.stat().st_size, source.stat().st_size]), \
                mock.patch.object(MODULE, "put_file") as put_file, \
                mock.patch.object(MODULE, "move") as move, \
                mock.patch.object(MODULE, "remote_hash") as remote_hash:
            MODULE.upload_verified(
                client, "/daily/.pending-generation", source,
                MODULE.sha256_file(source), remote_hash_required=False)
        put_file.assert_called_once()
        move.assert_called_once()
        remote_hash.assert_not_called()
        client.delete_if_present.assert_called_once()


if __name__ == "__main__":
    unittest.main()
