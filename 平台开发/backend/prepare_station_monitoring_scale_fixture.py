"""Build the isolated production-shape fixture used by station monitoring scale tests.

The default fixture contains 36 sites, 2,160,000 authenticated raw frames, and
468,000 projected business observations (1,000 slots for each of 13 factors at
each site). The output is always a new, explicitly named SQLite file and is
never allowed to reuse the application's configured database.
"""

import argparse
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
from migrate_station_ingestion import apply_migration


SITE_COUNT = 36
RAW_FRAME_COUNT = 2_160_000
SLOTS_PER_FACTOR = 1_000
FACTORS = (
    ('4C1A', 'ammonia'),
    ('4A11', 'codmn'),
    ('4818', 'conductivity'),
    ('4711', 'dissolved_oxygen'),
    ('4612', 'ph'),
    ('HJ212:029', 'total_copper'),
    ('HJ212:027', 'total_lead'),
    ('4E1A', 'total_nitrogen'),
    ('4F12', 'total_organic_carbon'),
    ('4D1B', 'total_phosphorus'),
    ('HJ212:022', 'total_tin'),
    ('HJ212:030', 'total_zinc'),
    ('4910', 'turbidity'),
)
OBSERVATION_COUNT = SITE_COUNT * len(FACTORS) * SLOTS_PER_FACTOR
CHUNK_SIZE = 20_000


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--force', action='store_true', help='replace only the explicitly named output file')
    return parser.parse_args()


def _prepare_empty_database(output: Path, force: bool) -> None:
    output = output.resolve()
    configured = Path(web_app.DB_PATH).resolve()
    if output == configured:
        raise RuntimeError('scale fixture output must not be the configured application database')
    if output.suffix.lower() not in {'.db', '.sqlite', '.sqlite3'}:
        raise RuntimeError('scale fixture output must be an explicitly named SQLite file')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if not force:
            raise RuntimeError(f'output already exists (pass --force to replace it): {output}')
        output.unlink()

    previous_database = web_app.DB_PATH
    backup_dir = output.parent / f'{output.stem}-migration-backups'
    try:
        web_app.DB_PATH = str(output)
        web_app.init_db()
        _, backup = apply_migration(output, backup_dir, isolated_test=True)
        if backup and backup.is_file():
            backup.unlink()
        if backup_dir.is_dir() and not any(backup_dir.iterdir()):
            backup_dir.rmdir()
    finally:
        web_app.DB_PATH = previous_database


def _chunks(total, factory):
    for start in range(0, total, CHUNK_SIZE):
        end = min(start + CHUNK_SIZE, total)
        yield [factory(index) for index in range(start, end)]


def build_fixture(output: Path, force: bool = False) -> None:
    output = output.resolve()
    started = time.perf_counter()
    _prepare_empty_database(output, force)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    raw_start = now - timedelta(seconds=RAW_FRAME_COUNT - 1)
    local_now = now.astimezone(ZoneInfo('Asia/Shanghai'))
    current_slot = local_now.replace(
        hour=(local_now.hour // 4) * 4, minute=0, second=0, microsecond=0)
    latest_complete_slot = current_slot - timedelta(hours=8)

    with closing(sqlite3.connect(output)) as connection:
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('PRAGMA journal_mode=OFF')
        connection.execute('PRAGMA synchronous=OFF')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('PRAGMA cache_size=-262144')

        explicit_indexes = connection.execute(
            """SELECT name,sql FROM sqlite_master
               WHERE type='index' AND sql IS NOT NULL
                 AND tbl_name IN ('ingest_raw_frames','observation_batches',
                                  'observation_values','monitoring_business_observations')
               ORDER BY name""").fetchall()
        for name, _ in explicit_indexes:
            connection.execute(f'DROP INDEX "{name}"')

        connection.executemany(
            """INSERT INTO sites(id,code,name,type,district,address,status)
               VALUES (?,?,?,'water','隔离规模测试区','隔离规模测试地址','online')""",
            ((site_id, f'PERF-{site_id:03d}', f'隔离规模站点{site_id:03d}')
             for site_id in range(1, SITE_COUNT + 1)),
        )
        connection.executemany(
            """INSERT INTO trusted_endpoints(
                   id,station_code,credential_hmac,business_site_id,enabled,endpoint_state)
               VALUES (?,?,?,?,1,'bound')""",
            ((site_id, f'PERF-MN-{site_id:03d}', f'isolated-hmac-{site_id}', site_id)
             for site_id in range(1, SITE_COUNT + 1)),
        )
        connection.executemany(
            """INSERT INTO monitoring_endpoint_profiles(
                   id,endpoint_id,business_site_id,instrument_asset_code,timezone,enabled,
                   expected_granularity,expected_interval_seconds,effective_from)
               VALUES (?,?,?,?, 'Asia/Shanghai',1,'periodic',14400,'2020-01-01T00:00:00+00:00')""",
            ((site_id, site_id, site_id, f'INST-{site_id:03d}')
             for site_id in range(1, SITE_COUNT + 1)),
        )
        mapping_rows = []
        mapping_id = 1
        for site_id in range(1, SITE_COUNT + 1):
            for protocol_code, business_metric in FACTORS:
                mapping_rows.append((
                    mapping_id, site_id, protocol_code, business_metric,
                    f'INST-{site_id:03d}', 14400, 900,
                    '2020-01-01T00:00:00+00:00',
                ))
                mapping_id += 1
        connection.executemany(
            """INSERT INTO monitoring_factor_mappings(
                   id,endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   expected_interval_seconds,tolerance_seconds,effective_from)
               VALUES (?,?,?,?,?,?,?,?)""",
            mapping_rows,
        )
        connection.executemany(
            """INSERT INTO monitoring_business_schedules(
                   id,endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                   tolerance_seconds,effective_from,enabled,result_delay_seconds)
               VALUES (?,?,NULL,'Asia/Shanghai',14400,'00:00:00',900,
                       '2020-01-01T00:00:00+00:00',1,7200)""",
            ((site_id, site_id) for site_id in range(1, SITE_COUNT + 1)),
        )
        connection.commit()

        def raw_row(index):
            endpoint_id = (index % SITE_COUNT) + 1
            received_at = (raw_start + timedelta(seconds=index)).isoformat()
            return (
                index + 1, endpoint_id, f'PERF-MN-{endpoint_id:03d}', received_at,
                f'isolated-raw-{index + 1}', b'\x00', 1,
                'valid', 'authenticated', 'accepted', 'persisted',
            )

        for rows in _chunks(RAW_FRAME_COUNT, raw_row):
            connection.executemany(
                """INSERT INTO ingest_raw_frames(
                       id,endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                       crc_status,authentication_status,disposition,persistence_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            connection.commit()

        factor_units = dict(connection.execute(
            """SELECT protocol_code,standard_unit FROM monitoring_factor_definitions
               WHERE protocol_code IN ({})""".format(','.join('?' * len(FACTORS))),
            tuple(code for code, _ in FACTORS),
        ).fetchall())
        if len(factor_units) != len(FACTORS):
            raise RuntimeError('the migrated schema is missing one or more scale-test factors')

        per_site = len(FACTORS) * SLOTS_PER_FACTOR

        def observation_facts(index):
            site_id = (index // per_site) + 1
            within_site = index % per_site
            factor_index = within_site // SLOTS_PER_FACTOR
            slot_index = within_site % SLOTS_PER_FACTOR
            protocol_code, business_metric = FACTORS[factor_index]
            scheduled_local = latest_complete_slot - timedelta(
                hours=4 * (SLOTS_PER_FACTOR - 1 - slot_index))
            scheduled_at = scheduled_local.astimezone(timezone.utc).isoformat()
            received_at = (scheduled_local.astimezone(timezone.utc) + timedelta(minutes=5)).isoformat()
            value = float((slot_index % 100) + factor_index) / 10.0
            return (
                index + 1, site_id, protocol_code, business_metric,
                scheduled_at, received_at, value, factor_units[protocol_code],
            )

        for start in range(0, OBSERVATION_COUNT, CHUNK_SIZE):
            end = min(start + CHUNK_SIZE, OBSERVATION_COUNT)
            facts = [observation_facts(index) for index in range(start, end)]
            connection.executemany(
                """INSERT INTO observation_batches(
                       id,raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                       reported_at,observed_at,received_at,granularity,aggregation_source,
                       idempotency_key,normalization_version,batch_status,projection_state,
                       is_current,normalized_at)
                   VALUES (?,?,?,?,2061,?,?,?,?, 'periodic','device_reported',?,
                           'station-monitoring-normalizer-v3','accepted','completed',1,?)""",
                ((row_id, row_id, site_id, site_id, row_id, scheduled_at, scheduled_at,
                  received_at, f'isolated-scale-{row_id}', received_at)
                 for row_id, site_id, _, _, scheduled_at, received_at, _, _ in facts),
            )
            connection.executemany(
                """INSERT INTO observation_values(
                       id,observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                       standard_value,standard_unit,quality,instrument_asset_code,parser_version,
                       is_published,is_current)
                   VALUES (?,?,?,?,?,?,?,?, 'valid',?,'isolated-scale',1,1)""",
                ((row_id, row_id, protocol_code, business_metric, value, unit, value, unit,
                  f'INST-{site_id:03d}')
                 for row_id, site_id, protocol_code, business_metric, _, _, value, unit in facts),
            )
            connection.executemany(
                """INSERT INTO monitoring_business_observations(
                       id,source_observation_value_id,observation_batch_id,schedule_id,endpoint_id,
                       business_site_id,protocol_code,business_metric,instrument_asset_code,
                       scheduled_at,observed_at,received_at,standard_value,standard_unit,quality,
                       timeliness,slot_state,projection_version,is_current)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'valid','on_time','selected',
                           'station-monitoring-business-v3',1)""",
                ((row_id, row_id, row_id, site_id, site_id, site_id, protocol_code,
                  business_metric, f'INST-{site_id:03d}', scheduled_at, scheduled_at,
                  received_at, value, unit)
                 for row_id, site_id, protocol_code, business_metric,
                 scheduled_at, received_at, value, unit in facts),
            )
            connection.commit()

        for _, sql in explicit_indexes:
            connection.execute(sql)
        connection.execute('ANALYZE')
        connection.commit()
        quick_check = connection.execute('PRAGMA quick_check').fetchone()[0]
        counts = {
            'sites': connection.execute('SELECT COUNT(*) FROM sites').fetchone()[0],
            'raw_frames': connection.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0],
            'observations': connection.execute(
                'SELECT COUNT(*) FROM monitoring_business_observations').fetchone()[0],
        }

    if quick_check != 'ok':
        raise RuntimeError(f'generated fixture failed SQLite quick_check: {quick_check}')
    elapsed = time.perf_counter() - started
    print(f'fixture={output}')
    print(f'counts={counts}')
    print(f'bytes={output.stat().st_size}')
    print(f'elapsed_seconds={elapsed:.3f}')


if __name__ == '__main__':
    args = _arguments()
    build_fixture(args.output, force=args.force)
