"""Isolated production-shape concurrency verification for station monitoring.

The script refuses the configured application database, starts a real Waitress
server, keeps authenticated HJ212 receipt and normalization active, and records
per-endpoint latency plus SQLite lock/data-integrity outcomes as JSON.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
from hj212_parser import hj212_crc, parse_hj212_frame
from sl651_server import IngestionStorage, credential_hmac
from station_monitoring import normalize_raw_frame


PATHS = (
    '/api/dashboard/summary',
    '/api/sites',
    '/api/station-monitoring/sites?scope=all',
    '/api/station-monitoring/access-summary',
    '/api/station-monitoring/sites/1/overview',
)
NORMALIZATION_VERSION = 'station-monitoring-normalizer-v1'
PASSWORD = 'r24-isolated-load'
PEPPER = 'r24-isolated-pepper'
ISOLATED_USER_ID_BASE = 100000


class _FileTime(ctypes.Structure):
    _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ('read_operations', ctypes.c_uint64), ('write_operations', ctypes.c_uint64),
        ('other_operations', ctypes.c_uint64), ('read_bytes', ctypes.c_uint64),
        ('write_bytes', ctypes.c_uint64), ('other_bytes', ctypes.c_uint64),
    ]


def _filetime_value(value):
    return (int(value.high) << 32) | int(value.low)


def _resource_snapshot(process_ids):
    if os.name != 'nt':
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.GetProcessTimes.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime),
    )
    kernel32.GetProcessIoCounters.argtypes = (ctypes.c_void_p, ctypes.POINTER(_IoCounters))
    idle = _FileTime(); kernel = _FileTime(); user = _FileTime()
    if not kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    processes = {}
    for process_id in process_ids:
        handle = kernel32.OpenProcess(0x1000, False, int(process_id))
        if not handle:
            continue
        try:
            created = _FileTime(); exited = _FileTime(); process_kernel = _FileTime(); process_user = _FileTime()
            io = _IoCounters()
            if kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(process_kernel), ctypes.byref(process_user)):
                processes[str(process_id)] = {
                    'cpu_100ns': _filetime_value(process_kernel) + _filetime_value(process_user),
                    'read_bytes': 0,
                    'write_bytes': 0,
                }
                if kernel32.GetProcessIoCounters(handle, ctypes.byref(io)):
                    processes[str(process_id)].update(
                        read_bytes=int(io.read_bytes), write_bytes=int(io.write_bytes))
        finally:
            kernel32.CloseHandle(handle)
    return {
        'system': (_filetime_value(idle), _filetime_value(kernel), _filetime_value(user)),
        'processes': processes,
    }


def _resource_delta(start, end, duration, cpu_limit):
    if not start or not end:
        return {'available': False}
    idle_delta = end['system'][0] - start['system'][0]
    kernel_delta = end['system'][1] - start['system'][1]
    user_delta = end['system'][2] - start['system'][2]
    total = kernel_delta + user_delta
    result = {
        'available': True,
        'system_cpu_percent': (100.0 * (total - idle_delta) / total) if total else 0.0,
        'process_cpu_percent_of_host': 0.0,
        'process_cpu_percent_of_limit': 0.0,
        'read_bytes': 0,
        'write_bytes': 0,
    }
    cpu_100ns = 0
    for process_id, final in end['processes'].items():
        initial = start['processes'].get(process_id)
        if not initial:
            continue
        cpu_100ns += final['cpu_100ns'] - initial['cpu_100ns']
        result['read_bytes'] += max(0, final['read_bytes'] - initial['read_bytes'])
        result['write_bytes'] += max(0, final['write_bytes'] - initial['write_bytes'])
    host_capacity = max(1, os.cpu_count() or 1) * max(duration, 0.001)
    result['process_cpu_percent_of_host'] = 100.0 * (cpu_100ns / 10_000_000) / host_capacity
    limited_capacity = max(1, cpu_limit) * max(duration, 0.001)
    result['process_cpu_percent_of_limit'] = 100.0 * (cpu_100ns / 10_000_000) / limited_capacity
    return result


def _limit_process_cpus(process_id, cpu_limit):
    available = max(1, os.cpu_count() or 1)
    cpu_limit = max(1, min(int(cpu_limit), available))
    if os.name == 'nt':
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.SetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
        handle = kernel32.OpenProcess(0x0200 | 0x1000, False, int(process_id))
        if not handle:
            raise RuntimeError(f'cannot open process {process_id} for CPU affinity')
        try:
            if not kernel32.SetProcessAffinityMask(handle, (1 << cpu_limit) - 1):
                raise RuntimeError(f'cannot set process {process_id} CPU affinity')
        finally:
            kernel32.CloseHandle(handle)
    elif hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(process_id, set(range(cpu_limit)))
    return cpu_limit


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _database_counts(database):
    with closing(sqlite3.connect(database, timeout=30)) as connection:
        connection.execute('PRAGMA busy_timeout=30000')
        return {
            'raw_frames': connection.execute(
                'SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0],
            'pending_frames': connection.execute(
                "SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state IN ('pending_parse','pending_reparse')"
            ).fetchone()[0],
            'business_observations': connection.execute(
                'SELECT COUNT(*) FROM monitoring_business_observations').fetchone()[0],
            'selected_observations': connection.execute(
                "SELECT COUNT(*) FROM monitoring_business_observations WHERE is_current=1 AND slot_state='selected'"
            ).fetchone()[0],
            'duplicate_observations': connection.execute(
                "SELECT COUNT(*) FROM monitoring_business_observations WHERE slot_state='duplicate'"
            ).fetchone()[0],
        }


def _page_bytes(connection, name):
    try:
        value = connection.execute(
            'SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=?', (name,)
        ).fetchone()[0]
        return int(value or 0)
    except sqlite3.Error:
        return None


def measure_pending_query(database):
    query = """SELECT raw.id FROM ingest_raw_frames raw
               LEFT JOIN monitoring_normalization_retries retry
                 ON retry.raw_frame_id=raw.id AND retry.normalization_version=?
               WHERE raw.id>? AND raw.persistence_state IN ('pending_parse', 'pending_reparse')
                 AND (retry.raw_frame_id IS NULL OR (retry.state!='exhausted'
                      AND (retry.next_attempt_at IS NULL OR retry.next_attempt_at<=?)))
               ORDER BY raw.id LIMIT ?"""
    params = (NORMALIZATION_VERSION, 0, datetime.now(timezone.utc).isoformat(), 64)
    with closing(sqlite3.connect(database, timeout=30, isolation_level=None)) as connection:
        connection.execute('PRAGMA busy_timeout=30000')
        connection.execute('PRAGMA cache_size=-2000')
        table_bytes = _page_bytes(connection, 'ingest_raw_frames')
        index_bytes = _page_bytes(connection, 'idx_ingest_raw_pending_normalization')
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        pending_count = connection.execute(
            "SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state IN ('pending_parse','pending_reparse')"
        ).fetchone()[0]
        if table_bytes is None:
            table_bytes = Path(database).stat().st_size
        if index_bytes is None:
            index_bytes = max(page_size, pending_count * 24)

        connection.execute('BEGIN')
        connection.execute('DROP INDEX idx_ingest_raw_pending_normalization')
        before_plan = [row[3] for row in connection.execute(
            'EXPLAIN QUERY PLAN ' + query, params).fetchall()]
        connection.execute('PRAGMA shrink_memory')
        started = time.perf_counter()
        connection.execute(query, params).fetchall()
        before_seconds = time.perf_counter() - started
        connection.rollback()

        after_plan = [row[3] for row in connection.execute(
            'EXPLAIN QUERY PLAN ' + query, params).fetchall()]
        connection.execute('PRAGMA shrink_memory')
        started = time.perf_counter()
        connection.execute(query, params).fetchall()
        after_seconds = time.perf_counter() - started
    return {
        'before_plan': before_plan,
        'after_plan': after_plan,
        'before_seconds': before_seconds,
        'after_seconds': after_seconds,
        'before_read_bound_bytes': table_bytes,
        'after_read_bound_bytes': index_bytes,
    }


def _make_hj212(sequence):
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    scheduled = now.replace(hour=(now.hour // 4) * 4, minute=0, second=0, microsecond=0)
    data_time = scheduled.strftime('%Y%m%d%H%M%S')
    qn = f'{now.strftime("%Y%m%d%H%M%S")}{sequence % 1000:03d}'
    cp = ';'.join((
        f'DataTime={data_time}',
        '022-Avg=0.10', '022-Flag=N',
        '027-Avg=0.20', '027-Flag=N',
        '029-Avg=0.30', '029-Flag=N',
        '030-Avg=0.40', '030-Flag=N',
    ))
    body = (
        f'QN={qn};ST=91;CN=2061;PW={PASSWORD};MN=PERF-MN-001;Flag=5;CP=&&{cp}&&'
    ).encode('ascii')
    return b'##' + f'{len(body):04d}'.encode('ascii') + body + hj212_crc(body).encode('ascii') + b'\r\n'


def _prepare_receipt_identity(database):
    with closing(sqlite3.connect(database, timeout=30)) as connection:
        connection.execute('PRAGMA busy_timeout=30000')
        connection.execute(
            'UPDATE trusted_endpoints SET credential_hmac=? WHERE station_code=?',
            (credential_hmac(PASSWORD.encode('ascii'), PEPPER), 'PERF-MN-001'),
        )
        connection.commit()


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _wait_for_server(port, process, timeout=30):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Waitress exited before readiness: {process.returncode}')
        try:
            with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, HTTPError, URLError):
            time.sleep(0.1)
    raise RuntimeError('Waitress readiness timeout')


def _isolated_authenticate_token(token):
    prefix = 'isolated-user-'
    if not token or not token.startswith(prefix):
        return None, [], 'AUTH_REQUIRED'
    try:
        user_index = int(token[len(prefix):])
    except ValueError:
        return None, [], 'AUTH_REQUIRED'
    if user_index < 0:
        return None, [], 'AUTH_REQUIRED'
    user_id = ISOLATED_USER_ID_BASE + user_index
    return ({
        'id': user_id,
        'username': f'isolated-concurrency-admin-{user_index}',
        'real_name': f'隔离并发管理员{user_index}',
        'role': 'admin',
        'roles': ['admin'],
    }, [], None)


def _run_server(database, port, threads):
    web_app.DB_PATH = str(Path(database).resolve())
    web_app.STATION_MONITORING_ACCESS_MODE = 'public'
    web_app._authenticate_token = _isolated_authenticate_token
    web_app._ensure_sqlite_wal_mode()
    from waitress import serve
    serve(web_app.app, host='127.0.0.1', port=port, threads=threads, channel_timeout=30)


def run_load(database, threads, duration, users, output, cpu_limit):
    database = Path(database).resolve()
    configured = Path(web_app.DB_PATH).resolve()
    if database == configured:
        raise RuntimeError('concurrency verification refuses the configured application database')
    if not database.is_file():
        raise RuntimeError(f'isolated database does not exist: {database}')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(database, timeout=30)) as connection:
        connection.execute('PRAGMA busy_timeout=30000')
        connection.execute('PRAGMA journal_mode=WAL')
    _prepare_receipt_identity(database)
    index_measurement = measure_pending_query(database)
    start_counts = _database_counts(database)

    port = _free_port()
    server_log = output.with_suffix('.server.log')
    log_handle = open(server_log, 'w', encoding='utf-8')
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), '--serve',
         '--database', str(database), '--port', str(port), '--threads', str(threads)],
        cwd=str(Path(__file__).resolve().parents[1]), stdout=log_handle,
        stderr=subprocess.STDOUT, text=True,
    )
    cpu_limit = _limit_process_cpus(os.getpid(), cpu_limit)
    _limit_process_cpus(process.pid, cpu_limit)
    stop = threading.Event()
    samples = defaultdict(list)
    errors = []
    stats = Counter()
    sample_lock = threading.Lock()
    storage = IngestionStorage(database, PEPPER)

    def receiver():
        sequence = 0
        while not stop.is_set():
            sequence += 1
            try:
                frame = parse_hj212_frame(_make_hj212(sequence))
                auth = storage.authenticate(frame)
                raw_id, duplicate = storage.persist_parsed(
                    frame, auth, datetime.now(timezone.utc).isoformat())
                stats['received'] += 1
                stats['raw_duplicates'] += int(duplicate)
                stats['last_raw_id'] = raw_id
            except Exception as exc:
                stats['receive_errors'] += 1
                if 'locked' in str(exc).lower():
                    stats['receive_lock_errors'] += 1
            stop.wait(0.2)

    def normalizer():
        after_id = 0
        while not stop.is_set():
            try:
                raw_ids = storage.pending_normalization_page(after_id, 64)
                if not raw_ids:
                    after_id = 0
                    stop.wait(0.05)
                    continue
                for raw_id in raw_ids:
                    result = normalize_raw_frame(database, raw_id)
                    stats[f'normalized_{result}'] += 1
                after_id = raw_ids[-1]
            except Exception as exc:
                stats['normalization_errors'] += 1
                if 'locked' in str(exc).lower():
                    stats['normalization_lock_errors'] += 1
                stop.wait(0.05)

    def user_loop(user_index):
        opener = build_opener(ProxyHandler({}))
        headers = {'Authorization': f'Bearer isolated-user-{user_index}'}
        path_index = user_index % len(PATHS)
        while not stop.is_set():
            path = PATHS[path_index % len(PATHS)]
            path_index += 1
            started = time.perf_counter()
            status = None
            body = b''
            try:
                request = Request(f'http://127.0.0.1:{port}{path}', headers=headers)
                with opener.open(request, timeout=10) as response:
                    status = response.status
                    body = response.read()
            except HTTPError as exc:
                status = exc.code
                body = exc.read()
            except Exception as exc:
                status = 0
                body = str(exc).encode('utf-8', errors='replace')
            elapsed = time.perf_counter() - started
            decoded = body[:500].decode('utf-8', errors='replace')
            with sample_lock:
                samples[path].append(elapsed)
                stats['requests'] += 1
                if status is None or status == 0 or status >= 500:
                    stats['request_failures'] += 1
                    if len(errors) < 50:
                        errors.append({'path': path, 'status': status, 'body': decoded})
                if 'database is locked' in decoded.lower():
                    stats['visible_lock_errors'] += 1
            stop.wait(0.02)

    workers = [threading.Thread(target=receiver, name='receiver', daemon=True),
               threading.Thread(target=normalizer, name='normalizer', daemon=True)]
    user_workers = [threading.Thread(
        target=user_loop, args=(index,), name=f'user-{index}', daemon=True
    ) for index in range(users)]
    started_at = None
    run_started = None
    resource_start = None
    resource_end = None
    try:
        _wait_for_server(port, process)
        started_at = datetime.now(timezone.utc).isoformat()
        run_started = time.monotonic()
        resource_start = _resource_snapshot((os.getpid(), process.pid))
        for worker in workers + user_workers:
            worker.start()
        while time.monotonic() - run_started < duration:
            if process.poll() is not None:
                raise RuntimeError(f'Waitress exited during load: {process.returncode}')
            time.sleep(0.5)
    finally:
        stop.set()
        for worker in workers + user_workers:
            worker.join(timeout=15)
        resource_end = _resource_snapshot((os.getpid(), process.pid))
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_handle.close()

    end_counts = _database_counts(database)
    latency = {}
    for path, values in samples.items():
        latency[path] = {
            'count': len(values),
            'p50_seconds': _percentile(values, 0.50),
            'p95_seconds': _percentile(values, 0.95),
            'p99_seconds': _percentile(values, 0.99),
            'max_seconds': max(values) if values else None,
        }
    elapsed = time.monotonic() - run_started
    result = {
        'database': str(database),
        'threads': threads,
        'cpu_limit': cpu_limit,
        'users': users,
        'distinct_user_ids': [ISOLATED_USER_ID_BASE + index for index in range(users)],
        'distinct_user_count': users,
        'duration_seconds': elapsed,
        'started_at': started_at,
        'finished_at': datetime.now(timezone.utc).isoformat(),
        'index_measurement': index_measurement,
        'start_counts': start_counts,
        'end_counts': end_counts,
        'count_deltas': {key: end_counts[key] - start_counts[key] for key in start_counts},
        'stats': dict(stats),
        'latency': latency,
        'errors': errors,
        'server_log': str(server_log),
        'resources': _resource_delta(resource_start, resource_end, elapsed, cpu_limit),
        'io_wait_percent': None,
        'io_wait_note': 'Windows isolated host does not expose Linux iowait; production iowait remains a release verification item.',
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return result


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--duration-seconds', type=int, default=60)
    parser.add_argument('--users', type=int, default=10)
    parser.add_argument('--cpu-limit', type=int, default=4)
    parser.add_argument('--output', type=Path)
    return parser.parse_args()


if __name__ == '__main__':
    arguments = _arguments()
    if arguments.serve:
        _run_server(arguments.database, arguments.port, arguments.threads)
    else:
        if arguments.output is None:
            raise SystemExit('--output is required for load verification')
        run_load(
            arguments.database, arguments.threads, arguments.duration_seconds,
            arguments.users, arguments.output, arguments.cpu_limit,
        )
