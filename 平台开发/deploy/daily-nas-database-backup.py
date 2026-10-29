#!/usr/bin/env python3
"""Create one SQLite snapshot and publish it as a verified WebDAV generation."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


CHUNK_SIZE = 1024 * 1024
GENERATION_FORMAT = "%Y%m%d-%H%M%S"
BACKUP_KIND = "water-monitor-daily-database"
COMPRESSED_BACKUP_FORMAT = "sqlite-gzip-v1"
COMPRESSED_DATABASE_FILENAME = "water.db.gz"
MAX_RECEIPT_BYTES = 1024 * 1024


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def positive_int(values: dict[str, str], name: str, default: int) -> int:
    raw = values.get(name, str(default))
    if not raw.isdigit() or int(raw) <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return int(raw)


@dataclass(frozen=True)
class Settings:
    staging_dir: Path
    webdav_program: Path
    webdav_config: Path
    remote_path: str
    share_name: str
    container_name: str
    keep_generations: int
    pending_max_hours: int
    pending_max_attempts: int

    @classmethod
    def read(cls, path: Path) -> "Settings":
        values = load_env(path)
        required = ("STAGING_DIR", "WEBDAV_PROGRAM", "WEBDAV_CONFIG", "NAS_REMOTE_PATH")
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise RuntimeError("missing backup settings: " + ",".join(missing))
        staging_dir = Path(values["STAGING_DIR"])
        if not staging_dir.is_absolute() or str(staging_dir).startswith("/opt/"):
            raise RuntimeError("STAGING_DIR must be an absolute non-/opt path")
        webdav_program = Path(values["WEBDAV_PROGRAM"])
        webdav_config = Path(values["WEBDAV_CONFIG"])
        if not webdav_program.is_file():
            raise RuntimeError("WEBDAV_PROGRAM is not readable")
        if not webdav_config.is_file():
            raise RuntimeError("WEBDAV_CONFIG is not readable")
        config_stat = webdav_config.stat()
        if config_stat.st_uid != 0 or config_stat.st_mode & 0o077:
            raise RuntimeError("WEBDAV_CONFIG must be root-owned and root-only")
        return cls(
            staging_dir=staging_dir,
            webdav_program=webdav_program,
            webdav_config=webdav_config,
            remote_path=values["NAS_REMOTE_PATH"].strip("/"),
            share_name=values.get("NAS_SHARE_NAME", "团队文件-水质设备运维"),
            container_name=values.get("CONTAINER_NAME", "water-monitor"),
            keep_generations=positive_int(values, "KEEP_GENERATIONS", 7),
            pending_max_hours=positive_int(values, "PENDING_MAX_HOURS", 48),
            pending_max_attempts=positive_int(values, "PENDING_MAX_ATTEMPTS", 5),
        )


def atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_quick_check(path: Path, label: str = "database") -> None:
    uri = "file:" + path.resolve().as_posix() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        result = database.execute("PRAGMA quick_check").fetchall()
    if result != [("ok",)]:
        raise RuntimeError(f"{label} quick_check failed")


def stream_restore_metadata(archive: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    try:
        with gzip.open(archive, "rb") as restored:
            for chunk in iter(lambda: restored.read(CHUNK_SIZE), b""):
                digest.update(chunk)
                size += len(chunk)
    except (EOFError, OSError) as exc:
        raise RuntimeError("compressed backup is not readable") from exc
    return {
        "filename": "water.db",
        "size": size,
        "sha256": digest.hexdigest(),
        "verification": "streamed_gzip_bytes_match_checked_snapshot",
    }


def compress_and_verify_snapshot(
        snapshot: Path, work_dir: Path, *, created_at: str | None = None) -> dict[str, object]:
    if not snapshot.is_file():
        raise RuntimeError("snapshot is not readable")
    sqlite_quick_check(snapshot, "source snapshot")
    source_size = snapshot.stat().st_size
    source_sha256 = sha256_file(snapshot)
    archive = work_dir / COMPRESSED_DATABASE_FILENAME
    archive_part = archive.with_suffix(archive.suffix + ".part")
    archive_part.unlink(missing_ok=True)
    try:
        with snapshot.open("rb") as source, archive_part.open("wb") as target:
            with gzip.GzipFile(
                    filename="water.db", mode="wb", fileobj=target,
                    compresslevel=6, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed, length=CHUNK_SIZE)
        archive_part.replace(archive)

        restore_stream = stream_restore_metadata(archive)
        if restore_stream["size"] != source_size or restore_stream["sha256"] != source_sha256:
            raise RuntimeError("compressed backup does not restore the source snapshot")

        manifest = {
            "backup_kind": BACKUP_KIND,
            "backup_format": COMPRESSED_BACKUP_FORMAT,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "database": {
                "filename": "water.db", "size": source_size,
                "sha256": source_sha256, "quick_check": "ok",
            },
            "archive": {
                "filename": archive.name, "size": archive.stat().st_size,
                "sha256": sha256_file(archive), "compression": "gzip",
            },
            "restore_stream": restore_stream,
            "source": "live_sqlite_backup_api",
            "verification": (
                "source_quick_check_gzip_stream_sha256_"
                "remote_sha256_and_receipt_before_atomic_publish"
            ),
        }
        atomic_json(work_dir / "manifest.json", manifest)
        snapshot.unlink()
        return manifest
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    finally:
        archive_part.unlink(missing_ok=True)


def docker_value(container: str, template: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", template, container],
        check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def create_snapshot(settings: Settings, work_dir: Path) -> dict[str, object]:
    mount_lines = docker_value(
        settings.container_name,
        '{{range .Mounts}}{{if eq .Destination "/app/backend/data"}}{{printf "%s\\t%s\\n" .Type .Source}}{{end}}{{end}}',
    ).splitlines()
    if len(mount_lines) != 1:
        raise RuntimeError("container must expose one business database mount")
    mount_type, source_text = mount_lines[0].split("\t", 1)
    source_dir = Path(source_text)
    if mount_type != "bind" or not source_dir.is_absolute():
        raise RuntimeError("business database mount must be one absolute host bind path")
    source_db = source_dir / "water.db"
    if not source_db.is_file():
        raise RuntimeError("business database is not readable")
    if os.stat(source_dir).st_dev == os.stat(settings.staging_dir).st_dev:
        raise RuntimeError("STAGING_DIR must be on a different filesystem from the business database")

    image_id = docker_value(settings.container_name, "{{.Image}}")
    if not image_id:
        raise RuntimeError("running container image ID is unavailable")
    part = work_dir / "water.db.part"
    final = work_dir / "water.db"
    subprocess.run([
        "docker", "run", "--rm", "--network", "none",
        "--mount", f"type=bind,src={source_dir},dst=/source",
        "--mount", f"type=bind,src={work_dir},dst=/backup",
        image_id, "python", "-c",
        "import sqlite3; s=sqlite3.connect('file:/source/water.db?mode=ro',uri=True); "
        "t=sqlite3.connect('/backup/water.db.part'); s.backup(t); "
        "t.close(); s.close()",
    ], check=True)
    if not part.is_file():
        raise RuntimeError("snapshot was not created on staging storage")
    part.replace(final)
    return compress_and_verify_snapshot(final, work_dir)


def load_webdav(settings: Settings):
    loader = importlib.machinery.SourceFileLoader("daily_database_webdav", str(settings.webdav_program))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("WebDAV program could not be loaded")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module.WebDav(module.load_env(settings.webdav_config))


def resolve_parent(client, settings: Settings) -> str:
    current = client.ensure_directory("/", settings.share_name, create=False)
    for component in (item for item in settings.remote_path.split("/") if item):
        current = client.ensure_directory(current, component)
    return current


def head_size(client, path: str) -> int | None:
    try:
        with client.request("HEAD", path, timeout=60) as response:
            length = response.headers.get("Content-Length", "")
            if response.status not in (200, 204) or not length.isdigit():
                raise RuntimeError("remote HEAD response is invalid")
            return int(length)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def remote_hash(client, path: str) -> str:
    digest = hashlib.sha256()
    with client.request("GET", path, timeout=24 * 60 * 60) as response:
        for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def put_file(client, path: str, source: Path) -> None:
    encoded = urllib.parse.urlsplit(client.url(path))
    connection_type = http.client.HTTPSConnection if client.protocol == "https" else http.client.HTTPConnection
    connection = connection_type(client.host, client.port, timeout=24 * 60 * 60)
    try:
        connection.putrequest("PUT", encoded.path)
        for key, value in client.headers.items():
            connection.putheader(key, value)
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(source.stat().st_size))
        connection.endheaders()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                connection.send(chunk)
        response = connection.getresponse()
        response.read()
        if response.status not in (200, 201, 204):
            raise RuntimeError(f"remote PUT failed with status {response.status}")
    finally:
        connection.close()


def move(client, source: str, destination: str, *, overwrite: bool = False) -> None:
    with client.request(
        "MOVE", source,
        extra_headers={"Destination": client.url(destination), "Overwrite": "T" if overwrite else "F"},
        timeout=300,
    ) as response:
        if response.status not in (200, 201, 204):
            raise RuntimeError(f"remote MOVE failed with status {response.status}")


def upload_verified(client, parent: str, source: Path, expected_sha: str, *, remote_hash_required: bool) -> None:
    destination = parent.rstrip("/") + "/" + source.name
    size = source.stat().st_size
    existing = head_size(client, destination)
    if existing is not None:
        if existing != size or (remote_hash_required and remote_hash(client, destination) != expected_sha):
            raise RuntimeError(f"remote collision for {source.name}")
        return
    temporary = parent.rstrip("/") + "/.uploading-" + uuid.uuid4().hex
    try:
        put_file(client, temporary, source)
        if head_size(client, temporary) != size:
            raise RuntimeError(f"remote verification failed for {source.name}")
        if remote_hash_required and remote_hash(client, temporary) != expected_sha:
            raise RuntimeError(f"remote digest mismatch for {source.name}")
        move(client, temporary, destination)
        if head_size(client, destination) != size:
            raise RuntimeError(f"remote final size mismatch for {source.name}")
    finally:
        client.delete_if_present(temporary)


def _read_generation_receipt(client, path: str) -> dict[str, object] | None:
    try:
        with client.request("GET", path, timeout=60) as response:
            if response.status == 404:
                return None
            if response.status != 200:
                raise RuntimeError(f"remote receipt GET failed with status {response.status}")
            payload = response.read(MAX_RECEIPT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if len(payload) > MAX_RECEIPT_BYTES:
        return None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _is_completed_generation_receipt(receipt: dict[str, object] | None, generation: str) -> bool:
    if not receipt:
        return False
    database = receipt.get("database")
    if not isinstance(database, dict):
        return False
    task_identity = receipt.get("backup_kind") == BACKUP_KIND or (
        receipt.get("backup_kind") is None
        and receipt.get("verification")
        == "local_quick_check_sha256_remote_size_and_receipt_before_atomic_publish"
    )
    common = (
        task_identity and receipt.get("status") == "complete"
        and receipt.get("generation") == generation
        and receipt.get("source") == "live_sqlite_backup_api"
        and _is_file_metadata(database, "water.db")
    )
    if not common:
        return False
    backup_format = receipt.get("backup_format")
    if backup_format is None:
        return receipt.get("archive") is None and receipt.get("restore_stream") is None
    if backup_format != COMPRESSED_BACKUP_FORMAT:
        return False
    archive = receipt.get("archive")
    restore_stream = receipt.get("restore_stream")
    return (
        isinstance(archive, dict)
        and _is_file_metadata(archive, COMPRESSED_DATABASE_FILENAME)
        and archive.get("compression") == "gzip"
        and isinstance(restore_stream, dict)
        and _is_file_metadata(restore_stream, "water.db")
        and database.get("quick_check") == "ok"
        and restore_stream.get("verification") == "streamed_gzip_bytes_match_checked_snapshot"
        and restore_stream.get("size") == database.get("size")
        and restore_stream.get("sha256") == database.get("sha256")
    )


def _is_file_metadata(value: dict[str, object], filename: str) -> bool:
    size = value.get("size")
    digest = value.get("sha256")
    return (
        value.get("filename") == filename
        and isinstance(size, int) and not isinstance(size, bool) and size > 0
        and isinstance(digest, str) and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest.lower())
    )


def pending_transfer(
        work_dir: Path, manifest: dict[str, object], state: dict[str, object]
) -> tuple[Path, str, bool]:
    state_format = state.get("backup_format")
    manifest_format = manifest.get("backup_format")
    if state_format is None:
        if manifest_format is not None or manifest.get("archive") is not None:
            raise RuntimeError("pending backup format is missing or inconsistent")
        metadata = manifest.get("database")
        expected_filename = "water.db"
        remote_hash_required = False
    else:
        if state_format != COMPRESSED_BACKUP_FORMAT or manifest_format != state_format:
            raise RuntimeError("pending compressed backup format is inconsistent")
        metadata = manifest.get("archive")
        expected_filename = COMPRESSED_DATABASE_FILENAME
        remote_hash_required = True
    if not isinstance(metadata, dict) or not _is_file_metadata(metadata, expected_filename):
        raise RuntimeError("pending transfer metadata is invalid")
    filename = str(metadata["filename"])
    source = work_dir / filename
    if source.parent != work_dir or not source.is_file():
        raise RuntimeError("pending transfer object is not readable")
    return source, str(metadata["sha256"]), remote_hash_required


def list_generations(client, parent: str) -> list[str]:
    body = (
        b'<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/>'
        b'<d:resourcetype/></d:prop></d:propfind>'
    )
    with client.request(
        "PROPFIND", parent, data=body,
        extra_headers={"Depth": "1", "Content-Type": "application/xml"}, timeout=60,
    ) as response:
        if response.status not in (200, 207):
            raise RuntimeError(f"remote generation listing failed with status {response.status}")
        document = ElementTree.fromstring(response.read())
    names = []
    for item in document.findall("{DAV:}response"):
        if item.find(".//{DAV:}resourcetype/{DAV:}collection") is None:
            continue
        href = item.findtext("{DAV:}href") or ""
        name = item.findtext(".//{DAV:}displayname") or urllib.parse.unquote(href.rstrip("/").rsplit("/", 1)[-1])
        try:
            datetime.strptime(name, GENERATION_FORMAT)
        except ValueError:
            continue
        receipt = _read_generation_receipt(
            client, parent.rstrip("/") + "/" + name + "/receipt.json")
        if _is_completed_generation_receipt(receipt, name):
            names.append(name)
    return sorted(set(names), reverse=True)


def delete_collection(client, path: str) -> None:
    with client.request("DELETE", path, timeout=300) as response:
        if response.status not in (200, 202, 204, 404):
            raise RuntimeError(f"remote DELETE failed with status {response.status}")


def publish_pending(settings: Settings, state: dict[str, object]) -> None:
    work_dir = settings.staging_dir / str(state["work_dir"])
    manifest_path = work_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    transfer, transfer_sha256, remote_hash_required = pending_transfer(work_dir, manifest, state)
    client = load_webdav(settings)
    parent = resolve_parent(client, settings)
    generation = str(state["generation"])
    final_remote = parent.rstrip("/") + "/" + generation
    pending_remote = parent.rstrip("/") + "/.pending-" + generation
    state_path = settings.staging_dir / "pending.json"
    if state.get("stage") == "remote_complete":
        state["stage"] = "remote_published"
        atomic_json(state_path, state)

    receipt_path = work_dir / "receipt.json"
    if not receipt_path.exists():
        receipt = dict(manifest, backup_kind=BACKUP_KIND, generation=generation, status="complete")
        atomic_json(receipt_path, receipt)

    if state.get("stage") not in {"remote_published", "rotation_complete"}:
        if head_size(client, final_remote.rstrip("/") + "/receipt.json") is None:
            remote_dir = client.find_directory(parent, ".pending-" + generation)
            if not remote_dir:
                remote_dir = client.ensure_directory(parent, ".pending-" + generation)
            upload_verified(
                client, remote_dir, transfer, transfer_sha256,
                remote_hash_required=remote_hash_required)
            upload_verified(
                client, remote_dir, manifest_path, sha256_file(manifest_path),
                remote_hash_required=True)
            upload_verified(
                client, remote_dir, receipt_path, sha256_file(receipt_path),
                remote_hash_required=True)
            move(client, pending_remote, final_remote)

        remote_receipt = final_remote + "/receipt.json"
        if remote_hash(client, remote_receipt) != sha256_file(receipt_path):
            raise RuntimeError("remote completion receipt mismatch")
        state["stage"] = "remote_published"
        atomic_json(state_path, state)

    if state.get("stage") != "rotation_complete":
        generations = list_generations(client, parent)
        for old in generations[settings.keep_generations:]:
            delete_collection(client, parent.rstrip("/") + "/" + old)
        state["stage"] = "rotation_complete"
        atomic_json(state_path, state)


def pending_is_stale(settings: Settings, state: dict[str, object], now: float) -> bool:
    if state.get("stage") in {"remote_complete", "remote_published", "rotation_complete"}:
        return False
    age = now - float(state.get("created_epoch", 0))
    return age > settings.pending_max_hours * 3600 or int(state.get("attempts", 0)) >= settings.pending_max_attempts


def abandon_pending(settings: Settings, state: dict[str, object]) -> None:
    generation = str(state.get("generation", ""))
    if generation:
        try:
            client = load_webdav(settings)
            parent = resolve_parent(client, settings)
            delete_collection(client, parent.rstrip("/") + "/.pending-" + generation)
        except Exception as exc:
            print("STALE_REMOTE_PENDING_CLEANUP=DEFERRED REASON=" + str(exc))
    work_dir = settings.staging_dir / str(state.get("work_dir", ""))
    if work_dir.parent == settings.staging_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
    (settings.staging_dir / "pending.json").unlink(missing_ok=True)
    print("DAILY_NAS_DATABASE_BACKUP_STALE_PENDING=ABANDONED")


def run(settings: Settings, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.staging_dir, 0o700)
    state_path = settings.staging_dir / "pending.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if pending_is_stale(settings, state, now):
            abandon_pending(settings, state)
            state = {}
    else:
        state = {}

    if not state:
        generation = datetime.fromtimestamp(now, timezone.utc).strftime(GENERATION_FORMAT)
        work_name = "pending-" + generation
        work_dir = settings.staging_dir / work_name
        work_dir.mkdir(mode=0o700)
        try:
            manifest = create_snapshot(settings, work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
        state = {
            "generation": generation, "work_dir": work_name, "created_epoch": now,
            "attempts": 0, "stage": "snapshot_ready", "database_sha256": manifest["database"]["sha256"],
        }
        if manifest.get("backup_format") is not None:
            state["backup_format"] = manifest["backup_format"]
        atomic_json(state_path, state)

    if state.get("stage") == "remote_complete":
        state["stage"] = "remote_published"
        atomic_json(state_path, state)

    if state.get("stage") != "rotation_complete":
        state["attempts"] = int(state.get("attempts", 0)) + 1
        atomic_json(state_path, state)
        publish_pending(settings, state)

    if state.get("stage") != "rotation_complete":
        raise RuntimeError("remote backup rotation is incomplete")

    shutil.rmtree(settings.staging_dir / str(state["work_dir"]), ignore_errors=True)
    state_path.unlink(missing_ok=True)
    print("DAILY_NAS_DATABASE_BACKUP=PASS")
    print("GENERATION=" + str(state["generation"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/etc/water-monitor/daily-nas-database-backup.env"))
    arguments = parser.parse_args()
    try:
        run(Settings.read(arguments.config))
    except Exception as exc:
        print("DAILY_NAS_DATABASE_BACKUP=FAIL", flush=True)
        print("REASON=" + str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
